/**
 * Live-browser tests: the guards, the form reader and the filler driven against
 * a REAL Chromium DOM rather than a string.
 *
 * The other suites in this package are deliberately browser-free so they run
 * anywhere in milliseconds. They prove the pure logic. They cannot prove that
 * `detectHardStops` finds a challenge widget inside a real document, that
 * `discoverQuestions` reads a real form, or that `createPlaywrightAdapter`
 * resolves a real element by its label. That is what these do.
 *
 * These run headless on purpose: the headed requirement is a rule about how the
 * ASSISTANT runs against a live employer site, so a human can watch and
 * intervene. A test fixture on the local filesystem has no human to protect and
 * no site to be honest with. `src/config.ts` still refuses HEADLESS=true for the
 * real runner, and `config.test.ts` covers that.
 *
 * Requires the Chromium binary: `npx playwright install chromium`.
 * Skipped automatically when it is absent, so `npm test` stays green on a
 * machine that has not installed it.
 */

import { fileURLToPath } from 'node:url';
import path from 'node:path';

import { chromium, type Browser, type Page } from 'playwright';
import { afterAll, describe, expect, it } from 'vitest';

import { discoverQuestions } from './discover.js';
import { createPlaywrightAdapter, fillAll, requiredFailures, type FieldSpec } from './fill.js';
import { classify, detectHardStops } from './guards.js';

const FIXTURES = path.resolve(fileURLToPath(new URL('../../tests/fixtures', import.meta.url)));

const fixtureUrl = (name: string): string =>
  `file://${path.join(FIXTURES, name).replace(/\\/g, '/')}`;

// Launched at module scope, not in beforeAll: `live()` below is evaluated while
// the describe bodies run, which happens BEFORE any beforeAll hook. Deciding
// availability in a hook would mean every test was already registered as skipped
// by the time the browser became available.
let browser: Browser | null = null;
let unavailableReason = '';

try {
  browser = await chromium.launch({ headless: true });
} catch (error) {
  // No browser binary on this machine: skip rather than fail the suite.
  unavailableReason = String(error).slice(0, 300);
  browser = null;
}

if (!browser) {
  console.warn(
    `[live-browser] skipping live tests, chromium did not launch: ${unavailableReason}\n` +
      '[live-browser] run "npx playwright install chromium" to enable them.',
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

describe('hard stops on a real DOM', () => {
  live()('finds the reCAPTCHA widget and refuses the page', async () => {
    const page = await open('recaptcha-wall.html');
    const findings = await detectHardStops(page);

    expect(findings.length).toBeGreaterThan(0);
    expect(findings.some((f) => f.kind === 'captcha')).toBe(true);
    expect(classify(findings)).toBe('captcha_detected');
    await page.close();
  }, 30_000);

  live()('finds a Cloudflare Turnstile interstitial', async () => {
    const page = await open('turnstile-wall.html');
    const findings = await detectHardStops(page);

    expect(findings.length).toBeGreaterThan(0);
    expect(['captcha_detected', 'bot_protection_detected']).toContain(classify(findings));
    await page.close();
  }, 30_000);

  live()('finds a DataDome challenge', async () => {
    const page = await open('datadome-wall.html');
    const findings = await detectHardStops(page);

    expect(findings.length).toBeGreaterThan(0);
    expect(['captcha_detected', 'bot_protection_detected']).toContain(classify(findings));
    await page.close();
  }, 30_000);

  live()('finds a login wall from the password field alone', async () => {
    const page = await open('login-wall.html');
    const findings = await detectHardStops(page);

    expect(findings.some((f) => f.kind === 'login')).toBe(true);
    expect(classify(findings)).toBe('login_required');
    await page.close();
  }, 30_000);

  live()('reports nothing on an ordinary application form', async () => {
    const page = await open('clean-application.html');
    const findings = await detectHardStops(page);

    // The counterweight. A guard that stops on every page would be useless: it
    // would turn the whole product into a manual review queue.
    expect(findings, JSON.stringify(findings)).toEqual([]);
    await page.close();
  }, 30_000);
});

describe('reading a real form', () => {
  live()('discovers every field with the right type and required flag', async () => {
    const page = await open('clean-application.html');
    const questions = await discoverQuestions(page);
    const byLabel = (needle: string) =>
      questions.find((q) => q.text.toLowerCase().includes(needle));

    expect(questions.length).toBeGreaterThanOrEqual(10);

    expect(byLabel('first name')?.required).toBe(true);
    expect(byLabel('email')?.required).toBe(true);
    expect(byLabel('phone')?.required).toBe(false);

    expect(byLabel('why do you want')?.type).toBe('long_text');
    expect(byLabel('resume')?.type).toBe('file');
    expect(byLabel('authorized to work')?.type).toBe('single_select');

    // The demographic question must be recognised as EEO so the server can
    // default it to "prefer not to say" instead of ever guessing.
    expect(byLabel('gender')?.type).toBe('eeo');

    // Options are reported as the visible LABEL text, not the value attribute,
    // because that is what the server matches an answer against: answers.py
    // picks the option whose text equals the yes/no it derived from a verified
    // fact, so that the platform's own wording is submitted.
    const workAuth = byLabel('authorized to work');
    expect(workAuth?.options).toEqual(expect.arrayContaining(['Yes', 'No']));

    const gender = byLabel('gender');
    expect(gender?.options).toEqual(expect.arrayContaining(['I prefer not to say']));

    await page.close();
  }, 30_000);
});

describe('filling a real form', () => {
  const SPECS: FieldSpec[] = [
    { selector_hint: 'first_name', question_external_id: 'first_name', label: 'First name', value: 'Test', type: 'short_text', required: true },
    { selector_hint: 'last_name', question_external_id: 'last_name', label: 'Last name', value: 'Owner', type: 'short_text', required: true },
    // name attribute differs from the hint: must fall back to id
    { selector_hint: 'email', question_external_id: 'email', label: 'Email address', value: 'owner@example.com', type: 'short_text', required: true },
    // resolvable only via data-qa
    { selector_hint: 'phone', question_external_id: 'phone', label: 'Phone number', value: '+1 415 555 0100', type: 'short_text', required: false },
    // resolvable only via label text
    { selector_hint: 'current_location', question_external_id: 'current_location', label: 'Current location (city)', value: 'Austin, TX, US', type: 'short_text', required: false },
    // resolvable only via placeholder / aria-label
    { selector_hint: 'linkedin', question_external_id: 'linkedin', label: 'LinkedIn profile', value: 'https://www.linkedin.com/in/testowner', type: 'short_text', required: false },
    { selector_hint: 'website', question_external_id: 'website', label: 'website', value: 'https://github.com/testowner', type: 'short_text', required: false },
    // select matched case-insensitively against the option text
    { selector_hint: 'work_auth', question_external_id: 'work_auth', label: 'Are you legally authorized to work in the job location?', value: 'Yes', type: 'single_select', required: true },
    { selector_hint: 'sponsorship', question_external_id: 'sponsorship', label: 'Will you now or in the future require visa sponsorship?', value: 'No', type: 'single_select', required: true },
    // yes/no mapped onto a checkbox
    { selector_hint: 'relocate', question_external_id: 'relocate', label: 'Are you willing to relocate?', value: 'no', type: 'boolean', required: false },
  ];

  live()('resolves every field through a different strategy and writes the value', async () => {
    const page = await open('clean-application.html');
    const results = await fillAll(createPlaywrightAdapter(page), SPECS);

    const failed = results.filter((r) => !r.ok);
    expect(failed, `unfilled: ${JSON.stringify(failed, null, 2)}`).toEqual([]);
    expect(requiredFailures(SPECS, results)).toEqual([]);

    // Read the values back out of the live DOM, not out of our own bookkeeping.
    expect(await page.inputValue('#first_name')).toBe('Test');
    expect(await page.inputValue('#email')).toBe('owner@example.com');
    expect(await page.inputValue('#phone-input')).toBe('+1 415 555 0100');
    expect(await page.inputValue('#loc')).toBe('Austin, TX, US');
    expect(await page.inputValue('#work_auth')).toBe('yes');
    expect(await page.inputValue('#sponsorship')).toBe('No');
    expect(await page.isChecked('#relocate')).toBe(false);

    // Nothing was typed into the free-text question: the server never supplied
    // an answer for it, and the assistant does not invent one.
    expect(await page.inputValue('#why')).toBe('');

    await page.close();
  }, 45_000);

  live()('reports a required field it cannot locate instead of inventing one', async () => {
    const page = await open('clean-application.html');
    const results = await fillAll(createPlaywrightAdapter(page), [
      {
        selector_hint: 'security_clearance_level',
        question_external_id: 'security_clearance_level',
        label: 'Security clearance level',
        value: 'Top Secret',
        type: 'short_text',
        required: true,
      },
    ]);

    expect(results[0]?.ok).toBe(false);
    expect(results[0]?.reason).toBeTruthy();
    await page.close();
  }, 30_000);

  live()('never writes a value the server did not supply', async () => {
    const page = await open('clean-application.html');
    await fillAll(createPlaywrightAdapter(page), [
      { selector_hint: 'first_name', question_external_id: 'first_name', label: 'First name', value: 'Test', type: 'short_text', required: true },
    ]);

    // Every other field is still exactly as the page rendered it.
    expect(await page.inputValue('#email')).toBe('');
    expect(await page.inputValue('#gender')).toBe('');
    expect(await page.inputValue('#why')).toBe('');
    await page.close();
  }, 30_000);
});
