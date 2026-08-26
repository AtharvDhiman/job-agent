"""The compliance rules are the product. These tests are the contract."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.connectors import registry
from app.connectors.base import BaseConnector, ConnectorRegistry
from app.connectors.http import PoliteClient
from app.core.enums import ComplianceTier, SubmissionPolicy

#: The only platforms whose application forms this project knows how to drive.
#: Written out literally so widening it is a deliberate, reviewable edit here as
#: well as in the connector, the registry and the browser assistant.
EXPECTED_BROWSER_SUPPORTED = {"greenhouse", "lever", "ashby", "workable", "smartrecruiters"}


def test_every_connector_declares_a_compliance_tier():
    for connector in registry.all():
        assert isinstance(connector.compliance_tier, ComplianceTier), connector.key
        assert isinstance(connector.submission_policy_default, SubmissionPolicy), connector.key
        assert connector.policy_note, f"{connector.key} must explain its policy to the user"


def test_registry_refuses_a_connector_without_a_policy():
    local = ConnectorRegistry()

    class Sneaky(BaseConnector):
        key = "sneaky"
        display_name = "Sneaky"
        # no compliance_tier, no submission_policy_default

        def fetch(self, spec, *, etag=""):
            return None

    with pytest.raises(TypeError, match="compliance"):
        local.register(Sneaky)


def test_linkedin_and_indeed_can_never_be_auto_submitted():
    for key in ("linkedin", "indeed"):
        connector = registry.get(key)
        assert connector.submission_policy_default is SubmissionPolicy.PROHIBITED
        assert connector.compliance_tier is ComplianceTier.PARTNER_API
        described = connector.describe()
        assert described["automation_permitted_for_submission"] is False
        assert described["requires_user_review_by_default"] is True


def test_no_connector_defaults_to_automated_submission():
    """Out of the box nothing submits by itself. Authorization is opt-in."""
    for connector in registry.all():
        assert connector.submission_policy_default in (
            SubmissionPolicy.REVIEW_REQUIRED,
            SubmissionPolicy.PROHIBITED,
        ), connector.key


def test_partner_connectors_are_unavailable_without_your_own_credentials():
    from app.core.config import settings

    for key in ("linkedin", "indeed", "adzuna"):
        available, reason = registry.get(key).is_available(settings)
        assert available is False
        assert "Requires" in reason


@pytest.mark.parametrize(
    "body",
    [
        '<div class="g-recaptcha" data-sitekey="x"></div>',
        "<p>Please enable JavaScript and cookies to continue</p>",
        "<title>Checking your browser before accessing</title>",
        "<div id=px-captcha></div>",
    ],
)
def test_bot_walls_abort_rather_than_being_worked_around(body):
    from app.connectors.base import BlockedByPolicyError

    with pytest.raises(BlockedByPolicyError):
        PoliteClient.assert_no_bot_wall("https://example.com/jobs", body)


def test_login_walls_abort():
    from app.connectors.base import BlockedByPolicyError

    with pytest.raises(BlockedByPolicyError, match="login"):
        PoliteClient.assert_no_bot_wall(
            "https://example.com/jobs", "<p>Please log in to view this posting</p>"
        )


def test_clean_page_passes():
    PoliteClient.assert_no_bot_wall(
        "https://example.com/jobs", "<h1>Senior Engineer</h1><p>Apply below.</p>"
    )


def test_the_registry_refuses_a_prohibited_key_that_declares_itself_automatable():
    """Registering 'linkedin' with a softer policy would make the hard list decorative."""
    local = ConnectorRegistry()

    class Impostor(BaseConnector):
        key = "LinkedIn"  # also checks that the key is normalised before matching
        display_name = "LinkedIn but allowed"
        compliance_tier = ComplianceTier.PARTNER_API
        submission_policy_default = SubmissionPolicy.AUTO_SUBMIT
        policy_note = "nope"

        def fetch(self, spec, *, etag=""):
            return None

    with pytest.raises(TypeError, match="PROHIBITED"):
        local.register(Impostor)


def test_the_registry_and_the_policy_layer_prohibit_the_same_platforms():
    from app.connectors.base import HARD_PROHIBITED_KEYS
    from app.services.policy import HARD_PROHIBITED_PLATFORMS

    assert HARD_PROHIBITED_KEYS == HARD_PROHIBITED_PLATFORMS


# --------------------------------------------------------------------------
# Browser submission is an allow-list, not "anything not prohibited"
# --------------------------------------------------------------------------
def test_only_the_five_supported_ats_platforms_may_be_driven_by_the_browser():
    assert set(registry.browser_submission_keys()) == EXPECTED_BROWSER_SUPPORTED


def test_discovery_only_connectors_do_not_claim_a_browser_workflow():
    """A public jobs feed is grounds for discovery, never for filling a form."""
    for connector in registry.all():
        if connector.key in EXPECTED_BROWSER_SUPPORTED:
            continue
        assert connector.browser_submission_supported is False, connector.key
        assert connector.describe()["automation_permitted_for_submission"] is False, connector.key


def test_every_browser_supported_connector_still_requires_an_explicit_grant():
    """Supporting a form is not the same as being allowed to submit one."""
    for key in sorted(EXPECTED_BROWSER_SUPPORTED):
        connector = registry.get(key)
        assert connector.submission_policy_default is SubmissionPolicy.REVIEW_REQUIRED, key
        assert connector.describe()["requires_user_review_by_default"] is True, key


def test_the_registry_refuses_a_prohibited_connector_that_claims_a_browser_workflow():
    """Opening and typing into a page is automation even if submit is never clicked."""
    local = ConnectorRegistry()

    class Overreaching(BaseConnector):
        key = "indeed"
        display_name = "Indeed but drivable"
        compliance_tier = ComplianceTier.PARTNER_API
        submission_policy_default = SubmissionPolicy.PROHIBITED
        policy_note = "nope"
        browser_submission_supported = True

        def fetch(self, spec, *, etag=""):
            return None

    with pytest.raises(TypeError, match="browser_submission_supported"):
        local.register(Overreaching)


# --------------------------------------------------------------------------
# The browser assistant holds its own copy of both lists. It has to: it is the
# process that actually opens somebody's site, so it must fail closed without
# asking the server. These tests are what stop the copies drifting apart.
# --------------------------------------------------------------------------
GUARDS_TS = Path(__file__).resolve().parents[3] / "browser-assistant" / "src" / "core" / "guards.ts"


def _ts_string_array(name: str) -> set[str]:
    """Read an `export const NAME: readonly string[] = [...]` literal out of TypeScript.

    A parse failure fails the test rather than returning an empty set. Comparing
    nothing against nothing would silently pass and hide the exact drift this
    test exists to catch.
    """
    assert GUARDS_TS.is_file(), f"Expected the browser assistant guard file at {GUARDS_TS}"
    source = GUARDS_TS.read_text(encoding="utf-8")
    match = re.search(
        rf"export const {re.escape(name)}\s*:\s*readonly string\[\]\s*=\s*\[([^\]]*)\]",
        source,
    )
    assert match is not None, (
        f"Could not find `export const {name}: readonly string[] = [...]` in {GUARDS_TS.name}. "
        "If the declaration moved or changed shape, fix this parser -- do not delete the test."
    )
    items = re.findall(r"""['"]([^'"]+)['"]""", match.group(1))
    assert items, f"{name} in {GUARDS_TS.name} parsed as empty"
    return {item.strip().lower() for item in items}


def test_the_browser_assistant_prohibits_exactly_what_the_registry_prohibits():
    from app.connectors.base import HARD_PROHIBITED_KEYS

    assert _ts_string_array("PROHIBITED_CONNECTORS") == set(HARD_PROHIBITED_KEYS)


def test_the_browser_assistant_allow_list_matches_the_registry():
    """Adding a connector in Python must not silently grant it browser access."""
    assert _ts_string_array("BROWSER_SUPPORTED_CONNECTORS") == set(
        registry.browser_submission_keys()
    )


@pytest.mark.parametrize("spelling", ["LinkedIn", " indeed ", "GREENHOUSE"])
def test_connectors_are_looked_up_case_insensitively(spelling):
    assert registry.get(spelling).key == spelling.strip().lower()


def test_manual_connector_never_fetches():
    from app.connectors.base import BlockedByPolicyError, SourceSpec

    connector = registry.get("manual")(http=None, settings=None)
    with pytest.raises(BlockedByPolicyError, match="never fetches"):
        connector.fetch(SourceSpec(connector_key="manual", identifier="https://x.example"))
