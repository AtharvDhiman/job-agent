/**
 * The per-portal registry, and the guarantees that must hold for every adapter
 * in it.
 *
 * Most of this is browser-free: host resolution and the shape of a declaration
 * are pure data questions and should be answered in milliseconds. The two live
 * cases at the bottom are the ones a string cannot answer - whether
 * checkFormSafety actually clears a real Greenhouse-shaped DOM, and whether it
 * actually refuses a real form that has an account gate in it.
 */

import { fileURLToPath } from 'node:url';
import path from 'node:path';

import { chromium, type Browser, type Page } from 'playwright';
import { afterAll, describe, expect, it } from 'vitest';

import {
  PORTAL_ADAPTERS,
  adapterForKey,
  adapterForUrl,
  checkFormSafety,
  hostMatches,
  toReviewReason,
  type PortalAdapter,
} from './index.js';
import { GREENHOUSE } from './greenhouse.js';
import { LEVER } from './lever.js';

/**
 * ReviewReason in backend/app/core/enums.py, hard-coded on purpose.
 *
 * Importing the list from the module under test would only prove the module
 * agrees with itself. Written out here, this array is the contract with the
 * server: if somebody adds a reason to an adapter that the backend enum does
 * not have, this file fails.
 */
const BACKEND_REVIEW_REASONS: string[] = [
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
];

const EXPECTED_KEYS = ['greenhouse', 'lever', 'ashby', 'workable', 'smartrecruiters'];

describe('resolving an adapter from a URL', () => {
  const cases: [string, string][] = [
    ['https://boards.greenhouse.io/northwindsystems/jobs/4188245', 'greenhouse'],
    ['https://job-boards.greenhouse.io/northwindsystems/jobs/4188245#app', 'greenhouse'],
    ['https://jobs.lever.co/northwind/6f0c1b1a-2f3d-4a55-9d2b-9a0b7a4c81e2/apply', 'lever'],
    ['https://jobs.ashbyhq.com/northwind/9c1f2a3b-0e4d-4d21-a0f5-1b2c3d4e5f60/application', 'ashby'],
    ['https://apply.workable.com/northwind-systems/j/A1B2C3D4E5/apply/', 'workable'],
    ['https://jobs.smartrecruiters.com/NorthwindSystems/743999912345678-platform-engineer', 'smartrecruiters'],
    ['https://careers.smartrecruiters.com/NorthwindSystems/platform-engineer', 'smartrecruiters'],
  ];

  for (const [url, key] of cases) {
    it(`resolves ${key} from ${url}`, () => {
      expect(adapterForUrl(url)?.key).toBe(key);
    });
  }

  it('resolves every declared portal from at least one realistic URL', () => {
    const resolved = new Set(cases.map(([url]) => adapterForUrl(url)?.key));
    for (const key of EXPECTED_KEYS) expect(resolved.has(key as PortalAdapter['key'])).toBe(true);
  });
});

describe('platforms that must never get an adapter', () => {
  // Compliance guarantee, not a nicety. docs/COMPLIANCE.md and
  // core/guards.ts both say LinkedIn and Indeed are discovery and review only.
  // An adapter is the thing that would give one of them browser submission
  // support, so the correct answer is always null.
  const forbidden = [
    'https://www.linkedin.com/jobs/view/3912345678/',
    'https://linkedin.com/jobs/collections/recommended/',
    'https://www.indeed.com/viewjob?jk=abcdef0123456789',
    'https://uk.indeed.com/viewjob?jk=abcdef0123456789',
  ];

  for (const url of forbidden) {
    it(`refuses ${url}`, () => {
      expect(adapterForUrl(url)).toBeNull();
    });
  }

  it('declares no host pattern belonging to a prohibited platform', () => {
    for (const adapter of PORTAL_ADAPTERS) {
      for (const pattern of adapter.hostPatterns) {
        expect(pattern.toLowerCase()).not.toContain('linkedin.com');
        expect(pattern.toLowerCase()).not.toContain('indeed.com');
      }
    }
  });
});

describe('lookalike hosts', () => {
  it('does not match a host that merely ends with our pattern somewhere in the middle', () => {
    expect(adapterForUrl('https://boards.greenhouse.io.evil.com/northwind/jobs/1')).toBeNull();
    expect(adapterForUrl('https://jobs.lever.co.phishing.example/northwind/apply')).toBeNull();
    expect(adapterForUrl('https://apply.workable.com.attacker.test/j/A1B2C3')).toBeNull();
  });

  it('does not match a pattern that only appears in the path or the query', () => {
    expect(adapterForUrl('https://evil.example/boards.greenhouse.io/jobs/1')).toBeNull();
    expect(adapterForUrl('https://evil.example/?next=https://jobs.lever.co/northwind')).toBeNull();
  });

  it('matches the host itself and its subdomains, and nothing else', () => {
    expect(hostMatches('boards.greenhouse.io', 'boards.greenhouse.io')).toBe(true);
    expect(hostMatches('eu.boards.greenhouse.io', 'boards.greenhouse.io')).toBe(true);
    expect(hostMatches('BOARDS.GREENHOUSE.IO.', 'boards.greenhouse.io')).toBe(true);
    expect(hostMatches('boards.greenhouse.io.evil.com', 'boards.greenhouse.io')).toBe(false);
    expect(hostMatches('notboards.greenhouse.io', 'boards.greenhouse.io')).toBe(false);
    expect(hostMatches('', 'boards.greenhouse.io')).toBe(false);
  });

  it('returns null for a URL it cannot parse', () => {
    expect(adapterForUrl('not a url')).toBeNull();
    expect(adapterForUrl('')).toBeNull();
  });
});

describe('resolving an adapter from a key', () => {
  for (const key of EXPECTED_KEYS) {
    it(`resolves ${key}`, () => {
      expect(adapterForKey(key)?.key).toBe(key);
    });
  }

  it('is case and whitespace tolerant', () => {
    expect(adapterForKey('  Greenhouse ')?.key).toBe('greenhouse');
  });

  it('is null for a key we do not integrate with', () => {
    expect(adapterForKey('linkedin')).toBeNull();
    expect(adapterForKey('indeed')).toBeNull();
    expect(adapterForKey('workday')).toBeNull();
    expect(adapterForKey('')).toBeNull();
  });
});

describe('every declaration is complete', () => {
  it('registers exactly the five supported portals, once each', () => {
    expect(PORTAL_ADAPTERS.map((adapter) => adapter.key).sort()).toEqual([...EXPECTED_KEYS].sort());
  });

  for (const adapter of PORTAL_ADAPTERS) {
    describe(adapter.key, () => {
      it('declares a display name, hosts and a submit control', () => {
        expect(adapter.displayName.trim()).not.toBe('');
        expect(adapter.hostPatterns.length).toBeGreaterThan(0);
        for (const pattern of adapter.hostPatterns) expect(pattern.trim()).not.toBe('');
        expect(adapter.submitSelector.trim()).not.toBe('');
      });

      it('declares at least one marker proving we are on its application form', () => {
        expect(adapter.requiredFormMarkers.length).toBeGreaterThan(0);
        for (const marker of adapter.requiredFormMarkers) expect(marker.trim()).not.toBe('');
      });

      it('declares field hints that are non-empty selector lists', () => {
        const hints = Object.entries(adapter.fieldHints);
        expect(hints.length).toBeGreaterThan(0);
        for (const [field, selectors] of hints) {
          expect(selectors.length, field).toBeGreaterThan(0);
          for (const selector of selectors) expect(selector.trim(), field).not.toBe('');
        }
      });

      it('refuses a sign-in gate', () => {
        const login = adapter.unsupportedMarkers.filter((marker) => marker.reason === 'login_required');
        expect(login.length).toBeGreaterThan(0);
        for (const marker of login) expect(marker.selector.trim()).not.toBe('');
      });

      it('uses only reasons the backend enum knows', () => {
        for (const marker of adapter.unsupportedMarkers) {
          expect(BACKEND_REVIEW_REASONS, marker.selector).toContain(marker.reason);
        }
      });
    });
  }
});

describe('reasons handed back to the server', () => {
  it('translates the assistant vocabulary onto the backend enum', () => {
    // classify() calls an unanswerable question "unknown_question"; the server
    // enum spells the same state "unanswerable_question".
    expect(toReviewReason('unknown_question')).toBe('unanswerable_question');
    expect(BACKEND_REVIEW_REASONS).toContain(toReviewReason('unknown_question'));
  });

  it('passes through the reasons that already match', () => {
    for (const reason of ['captcha_detected', 'bot_protection_detected', 'login_required', 'robots_disallowed']) {
      expect(toReviewReason(reason)).toBe(reason);
    }
  });

  it('never invents a reason the server cannot key on', () => {
    expect(toReviewReason('something_new')).toBe('submission_error');
    expect(toReviewReason('')).toBe('submission_error');
  });
});

// ---------------------------------------------------------------------------
// Live Chromium
// ---------------------------------------------------------------------------

const FIXTURES = path.resolve(fileURLToPath(new URL('../../tests/fixtures', import.meta.url)));

const fixtureUrl = (name: string): string => `file://${path.join(FIXTURES, name).replace(/\\/g, '/')}`;

// Launched at module scope, not in beforeAll: `live()` is evaluated while the
// describe bodies run, which happens BEFORE any beforeAll hook. Deciding
// availability in a hook would register every test as skipped first.
let browser: Browser | null = null;
let unavailableReason = '';

try {
  browser = await chromium.launch({ headless: true });
} catch (error) {
  unavailableReason = String(error).slice(0, 300);
  browser = null;
}

if (!browser) {
  console.warn(
    `[platforms] skipping live tests, chromium did not launch: ${unavailableReason}\n` +
      '[platforms] run "npx playwright install chromium" to enable them.',
  );
}

afterAll(async () => {
  await browser?.close();
});

async function open(fixture: string): Promise<Page> {
  if (!browser) throw new Error(`chromium unavailable: ${unavailableReason}`);
  const page = await browser.newPage();
  await page.goto(fixtureUrl(fixture), { waitUntil: 'domcontentloaded' });
  return page;
}

const live = () => (browser ? it : it.skip);

describe('checkFormSafety against a real Greenhouse-shaped form', () => {
  live()('clears an ordinary application form', async () => {
    const page = await open('greenhouse-form.html');
    const result = await checkFormSafety(page, GREENHOUSE);

    expect(result.findings, JSON.stringify(result.findings)).toEqual([]);
    expect(result.safe, result.reason).toBe(true);
    expect(result.reason).toBe('');
    await page.close();
  }, 30_000);

  live()('finds every required form marker and the submit control', async () => {
    const page = await open('greenhouse-form.html');

    for (const marker of GREENHOUSE.requiredFormMarkers) {
      expect(await page.locator(marker).count(), marker).toBeGreaterThan(0);
    }
    expect(await page.locator(GREENHOUSE.submitSelector).count()).toBeGreaterThan(0);
    await page.close();
  }, 30_000);

  live()('resolves the real inputs through its field hints', async () => {
    const page = await open('greenhouse-form.html');

    // Each hint list is a ladder: the assertion is that SOME rung reaches the
    // real element, not that the first one does. That is how the runner uses
    // them, and it is why a broad fallback is allowed to sit behind a precise
    // selector that only some boards ship.
    const expected: Record<string, string> = {
      first_name: '#first_name',
      last_name: '#last_name',
      email: '#email',
      phone: '#phone',
      resume: '#resume',
      cover_letter: '#cover_letter',
      linkedin: '#linkedin_url',
    };

    for (const [field, element] of Object.entries(expected)) {
      const hints = GREENHOUSE.fieldHints[field];
      expect(hints, field).toBeDefined();
      let resolved = '';
      for (const selector of hints ?? []) {
        if ((await page.locator(selector).count()) > 0) {
          resolved = selector;
          break;
        }
      }
      expect(resolved, `no hint for "${field}" matched anything`).not.toBe('');
      // The rung that matched must reach the element we meant, not merely
      // something on the page.
      expect(await page.locator(element).count(), field).toBe(1);
      const matchesTarget = await page
        .locator(resolved)
        .first()
        .evaluate((node: Element, target: string) => node.matches(target), element);
      expect(matchesTarget, `${field}: "${resolved}" matched something other than ${element}`).toBe(true);
    }

    await page.close();
  }, 45_000);

  live()('refuses a page on the right host that is not the form we expected', async () => {
    // clean-application.html is a real form, but not a Greenhouse one: none of
    // the required markers are there. Filling it would be typing the user's
    // details into a form nobody vouched for.
    const page = await open('clean-application.html');
    const result = await checkFormSafety(page, GREENHOUSE);

    expect(result.safe).toBe(false);
    expect(result.reason).toBe('validation_failed');
    expect(BACKEND_REVIEW_REASONS).toContain(result.reason);
    await page.close();
  }, 30_000);
});

describe('checkFormSafety against a Lever form with an account gate', () => {
  live()('refuses it with login_required', async () => {
    const page = await open('lever-form.html');
    const result = await checkFormSafety(page, LEVER);

    expect(result.safe).toBe(false);
    expect(result.reason).toBe('login_required');
    expect(BACKEND_REVIEW_REASONS).toContain(result.reason);
    expect(result.findings.some((finding) => finding.kind === 'login')).toBe(true);
    await page.close();
  }, 30_000);

  live()('refuses it before it ever looks at the fields it could have filled', async () => {
    const page = await open('lever-form.html');

    // The form is entirely fillable. That is exactly why the refusal has to
    // come from the guard pass and not from an inability to find the fields.
    expect(await page.locator('input[name="name"]').count()).toBe(1);
    expect(await page.locator('input[name="email"]').count()).toBe(1);
    for (const marker of LEVER.requiredFormMarkers) {
      expect(await page.locator(marker).count(), marker).toBeGreaterThan(0);
    }

    const result = await checkFormSafety(page, LEVER);
    expect(result.safe).toBe(false);
    await page.close();
  }, 30_000);
});
