/**
 * Per-portal integrations.
 *
 * One generic form filler that guesses at every site is a filler that will
 * eventually guess wrong on somebody's real job application. So each supported
 * ATS is declared separately here: which hosts it owns, what proves we are
 * actually looking at its application form, which of its fields we know by
 * name, and what on its page means "this needs something we will not do".
 *
 * Two rules shape every declaration below:
 *
 *   1. An adapter is additive. It layers hints on top of the generic locator
 *      ladder in core/fill.ts; it never replaces a guard and never grants
 *      permission. core/guards.ts still decides whether a page may be touched,
 *      and the backend policy gate still decides whether a submit may be
 *      clicked. Nothing here can widen either.
 *   2. Where the real DOM is not known with certainty, the selector is broad
 *      and the comment says so. A broad-but-safe selector costs at most one
 *      review task. A precise-looking invented one is a lie that fails silently
 *      on a page nobody is watching.
 */

import {
  classify,
  detectHardStops,
  urlIsProhibited,
  type GuardFinding,
  type GuardKind,
  type GuardPage,
} from '../core/guards.js';

import { GREENHOUSE } from './greenhouse.js';
import { LEVER } from './lever.js';
import { ASHBY } from './ashby.js';
import { WORKABLE } from './workable.js';
import { SMARTRECRUITERS } from './smartrecruiters.js';

// ---------------------------------------------------------------------------
// The contract with the server
// ---------------------------------------------------------------------------

/**
 * ReviewReason in backend/app/core/enums.py, copied verbatim.
 *
 * Every reason this module hands back is one of these. The server keys review
 * tasks off the enum, so a reason string invented here would arrive as an
 * unexplained failure rather than as a review task with a written cause.
 */
export const REVIEW_REASONS = [
  'below_auto_submit_threshold',
  'platform_not_authorized',
  'platform_prohibits_automation',
  'unsupported_platform',
  'captcha_detected',
  'login_required',
  'bot_protection_detected',
  'robots_disallowed',
  'unanswerable_question',
  'free_text_question',
  'missing_verified_fact',
  'fact_guard_flagged',
  'validation_failed',
  'missing_attachment',
  'daily_limit_reached',
  'automation_disabled',
  'submission_error',
  'manual_request',
] as const;

export type ReviewReasonValue = (typeof REVIEW_REASONS)[number];

export function isReviewReason(value: string): value is ReviewReasonValue {
  return (REVIEW_REASONS as readonly string[]).includes(value);
}

export type PortalKey = 'greenhouse' | 'lever' | 'ashby' | 'workable' | 'smartrecruiters';

/** A page element whose presence means we have to stop and hand back. */
export interface UnsupportedMarker {
  selector: string;
  /** Why this marker disqualifies the page. Typed, so the enum is enforced at compile time. */
  reason: ReviewReasonValue;
}

export interface PortalAdapter {
  key: PortalKey;
  displayName: string;
  /**
   * Hostnames this adapter owns. Compared as suffixes against the parsed
   * hostname, never as a substring of the whole URL.
   */
  hostPatterns: string[];
  /** The form's submit control. Only ever clicked by the runner, never by this module. */
  submitSelector: string;
  /**
   * Selectors that must ALL be present for the page to count as this portal's
   * application form. A marketing page, an expired posting or a redirect that
   * happens to live on the right host fails here rather than getting filled.
   */
  requiredFormMarkers: string[];
  /** question_external_id -> candidate CSS selectors, most specific first. */
  fieldHints: Record<string, string[]>;
  /** Present means the form wants an account, an identity provider or a challenge. */
  unsupportedMarkers: UnsupportedMarker[];
}

export const PORTAL_ADAPTERS: PortalAdapter[] = [
  GREENHOUSE,
  LEVER,
  ASHBY,
  WORKABLE,
  SMARTRECRUITERS,
];

// ---------------------------------------------------------------------------
// Resolution
// ---------------------------------------------------------------------------

/**
 * Suffix match on the parsed hostname.
 *
 * Deliberately not a regex over the URL: "boards.greenhouse.io.evil.com" and
 * "evil.com/?x=boards.greenhouse.io" both contain a pattern we trust, and
 * neither is Greenhouse. Only the real host, or a subdomain of it, matches.
 */
export function hostMatches(host: string, pattern: string): boolean {
  const left = host.trim().toLowerCase().replace(/\.$/u, '');
  const right = pattern.trim().toLowerCase().replace(/\.$/u, '');
  if (left === '' || right === '') return false;
  return left === right || left.endsWith(`.${right}`);
}

/**
 * The adapter for an apply URL, or null when we have no declared integration.
 *
 * Null is the safe answer: with no adapter the runner has no per-portal
 * submission support, which is exactly the state LinkedIn and Indeed must
 * always be in. That is enforced twice - they own no host pattern here, and
 * urlIsProhibited() refuses them outright before any pattern is consulted.
 */
export function adapterForUrl(url: string): PortalAdapter | null {
  let host: string;
  try {
    host = new URL(url).hostname;
  } catch {
    return null; // An unparseable URL gets no integration.
  }

  if (urlIsProhibited(url)) return null;

  for (const adapter of PORTAL_ADAPTERS) {
    for (const pattern of adapter.hostPatterns) {
      if (hostMatches(host, pattern)) return adapter;
    }
  }
  return null;
}

export function adapterForKey(key: string): PortalAdapter | null {
  const wanted = (key ?? '').trim().toLowerCase();
  return PORTAL_ADAPTERS.find((adapter) => adapter.key === wanted) ?? null;
}

// ---------------------------------------------------------------------------
// Form safety
// ---------------------------------------------------------------------------

export interface FormSafetyResult {
  safe: boolean;
  findings: GuardFinding[];
  /** A ReviewReason when unsafe; empty when safe, because nothing is wrong to report. */
  reason: ReviewReasonValue | '';
}

/**
 * classify() speaks the assistant's abort vocabulary, which names one case
 * differently from the server enum. Anything unrecognised becomes
 * submission_error rather than being passed through: an unknown reason would
 * reach the server as a broken review task.
 */
export function toReviewReason(reason: string): ReviewReasonValue {
  if (reason === 'unknown_question') return 'unanswerable_question';
  return isReviewReason(reason) ? reason : 'submission_error';
}

function kindForReason(reason: ReviewReasonValue): GuardKind {
  switch (reason) {
    case 'captcha_detected':
      return 'captcha';
    case 'bot_protection_detected':
      return 'bot_protection';
    case 'login_required':
      return 'login';
    case 'robots_disallowed':
      return 'robots';
    default:
      return 'question';
  }
}

/** Present on the page? A selector the engine rejects counts as absent. */
async function present(page: GuardPage, selector: string): Promise<boolean> {
  try {
    return (await page.locator(selector).count()) > 0;
  } catch {
    return false;
  }
}

/**
 * Is this page one we may fill?
 *
 * Three passes, in this order, all failing closed:
 *
 *   1. The hard stops from core/guards.ts. A CAPTCHA, a bot check, a login wall
 *      or a paywall ends it here, before any portal-specific reasoning gets a
 *      chance to talk us past it.
 *   2. Every requiredFormMarker must exist. If they do not, we are not on the
 *      application form we expected, and filling whatever IS there would be
 *      typing a stranger's details into an unknown form.
 *   3. The portal's own unsupportedMarkers. Reported as unsupported_platform,
 *      with the marker's specific reason carried in the finding detail so the
 *      review task can say which capability was missing.
 */
export async function checkFormSafety(
  page: GuardPage,
  adapter: PortalAdapter,
): Promise<FormSafetyResult> {
  const hardStops = await detectHardStops(page);
  if (hardStops.length > 0) {
    return { safe: false, findings: hardStops, reason: toReviewReason(classify(hardStops)) };
  }

  for (const selector of adapter.requiredFormMarkers) {
    if (await present(page, selector)) continue;
    return {
      safe: false,
      reason: 'validation_failed',
      findings: [
        {
          kind: 'question',
          marker: selector,
          detail: `This does not look like the ${adapter.displayName} application form: nothing matches "${selector}".`,
          source: 'selector',
        },
      ],
    };
  }

  for (const marker of adapter.unsupportedMarkers) {
    if (!(await present(page, marker.selector))) continue;
    return {
      safe: false,
      reason: 'unsupported_platform',
      findings: [
        {
          kind: kindForReason(marker.reason),
          marker: marker.selector,
          detail: `${adapter.displayName} form needs something this assistant will not do (${marker.reason}).`,
          source: 'selector',
        },
      ],
    };
  }

  return { safe: true, findings: [], reason: '' };
}
