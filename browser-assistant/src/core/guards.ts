/**
 * Hard stops. This is the most important file in the program.
 *
 * docs/COMPLIANCE.md section 3 lists the situations in which this assistant is
 * required to stop, hand the page back to the human, and let the server open a
 * review task. None of them are recoverable by trying harder:
 *
 *   CAPTCHA or bot check   -> we never solve, never outsource, never evade
 *   login / account wall   -> we hold no credentials and never ask for any
 *   paywall or gated page  -> same treatment as a login wall
 *   robots.txt Disallow    -> we do not fetch or fill what we are asked not to
 *
 * Everything here fails CLOSED. A false positive costs one review task. A false
 * negative means the program did something it promised the user it would not do.
 */

export type GuardKind = 'captcha' | 'bot_protection' | 'login' | 'paywall' | 'robots' | 'question';

export interface GuardFinding {
  kind: GuardKind;
  /** The literal marker that matched, so the review task can show its reasoning. */
  marker: string;
  /** Human readable vendor or explanation. */
  detail: string;
  /** Where the marker was seen. */
  source: 'selector' | 'markup' | 'text' | 'url' | 'robots' | 'server';
}

/**
 * Things this program refuses to do. Printed at startup so the refusal is
 * visible before anything runs, not buried in a document nobody opens.
 */
export const NEVER_DO: string[] = [
  'Solve a CAPTCHA, or send one to a human or paid solving service.',
  'Patch navigator.webdriver or any other automation flag.',
  'Spoof a browser fingerprint, or mask the user agent to evade detection.',
  'Rotate proxies, residential IPs, or otherwise disguise where the traffic comes from.',
  'Run headless, or hide the browser window from you.',
  'Type a username, password, or any credential into a login form.',
  'Click through a consent banner, cookie wall, or terms-of-service gate on your behalf.',
  'Invent an answer, a date, an employer, a salary, a visa status, or a link.',
  'Fill or submit a page that robots.txt disallows for our user agent.',
  'Open, fill, or submit anything on LinkedIn or Indeed, whatever the server says.',
  'Retry an attempt that stopped for a policy reason.',
  'Report a submission it did not actually make.',
];

// ---------------------------------------------------------------------------
// Platforms this program never automates
// ---------------------------------------------------------------------------

/**
 * Mirrors HARD_PROHIBITED_PLATFORMS in backend/app/services/policy.py.
 *
 * The backend gate is the primary control, but this process is the one that
 * actually opens a browser at somebody's site. It does not get to say "the
 * server told me to": a bug, a stale row or a tampered response upstream must
 * not be able to point this program at a platform whose terms forbid it.
 */
export const PROHIBITED_CONNECTORS: readonly string[] = ['linkedin', 'indeed'];

/** Hostnames that belong to those platforms, checked against the apply URL. */
const PROHIBITED_HOST_PATTERN = /(^|\.)(linkedin\.com|indeed\.com|indeed\.[a-z.]{2,6})$/iu;

export function connectorIsProhibited(connectorKey: string): boolean {
  return PROHIBITED_CONNECTORS.includes((connectorKey ?? '').trim().toLowerCase());
}

/** True when the URL points at a platform we never automate. */
export function urlIsProhibited(url: string): boolean {
  let host: string;
  try {
    host = new URL(url).hostname;
  } catch {
    return false; // An unparseable URL is refused elsewhere; it is not evidence here.
  }
  return PROHIBITED_HOST_PATTERN.test(host);
}

export interface TaskLike {
  connector_key: string;
  apply_url: string;
  mode: string;
  may_click_submit: boolean;
}

/**
 * May this program open the page for this task at all? Called before the
 * browser is launched, so a refused task costs nothing and touches nobody.
 */
export function taskIsAllowed(task: TaskLike): { allowed: boolean; reason: string } {
  if (connectorIsProhibited(task.connector_key)) {
    return {
      allowed: false,
      reason: `${task.connector_key} prohibits automated applying. This assistant refuses the task regardless of what the server sent.`,
    };
  }
  if (urlIsProhibited(task.apply_url)) {
    return {
      allowed: false,
      reason: `${task.apply_url} is on a platform whose terms forbid automated applying. Apply to it yourself.`,
    };
  }
  return { allowed: true, reason: 'Platform is not on the never-automate list.' };
}

/**
 * May the submit button be clicked? Three independent things must agree: the
 * platform is automatable at all, the server authorized the click, and the
 * attempt was actually handed out as an auto-submit run rather than an assisted
 * autofill that happens to carry a stale flag.
 */
export function mayClickSubmit(task: TaskLike): boolean {
  if (!taskIsAllowed(task).allowed) return false;
  if (task.mode !== 'auto_submit') return false;
  return task.may_click_submit === true;
}

// ---------------------------------------------------------------------------
// CAPTCHA and bot protection
// ---------------------------------------------------------------------------

export const CAPTCHA_SELECTORS: string[] = [
  // Google reCAPTCHA (v2 checkbox, v2 invisible, enterprise)
  '.g-recaptcha',
  'iframe[src*="recaptcha"]',
  'iframe[src*="google.com/recaptcha"]',
  'textarea#g-recaptcha-response',
  'script[src*="recaptcha"]',
  // hCaptcha
  '.h-captcha',
  'iframe[src*="hcaptcha.com"]',
  'script[src*="hcaptcha.com"]',
  // Cloudflare Turnstile and the Cloudflare interstitial
  '.cf-turnstile',
  'iframe[src*="challenges.cloudflare.com"]',
  '#cf-challenge-running',
  '#challenge-form',
  // DataDome
  '#datadome-captcha',
  'iframe[src*="captcha-delivery.com"]',
  'script[src*="datadome"]',
  // PerimeterX / HUMAN
  '#px-captcha',
  'script[src*="perimeterx"]',
  'script[src*="px-cdn"]',
  // Kasada
  'script[src*="kasada"]',
  'script[src*="kpsdk"]',
  // Akamai Bot Manager
  'input[name="sensor_data"]',
  'script[src*="akam/"]',
];

export const CAPTCHA_TEXT_MARKERS: string[] = [
  'are you a robot',
  "i'm not a robot",
  'i am not a robot',
  'checking your browser',
  'enable javascript and cookies to continue',
  'verify you are human',
  'verify you are a human',
  'please complete the security check',
  'complete the captcha',
  'unusual traffic from your',
  'additional verification required',
];

/**
 * Markup signatures. Matched against the raw HTML so the pure, string-only
 * detector sees exactly what the live selector pass would see.
 */
interface MarkupSignature {
  marker: string;
  vendor: string;
  kind: GuardKind;
}

export const CAPTCHA_MARKUP_SIGNATURES: MarkupSignature[] = [
  { marker: 'g-recaptcha', vendor: 'Google reCAPTCHA', kind: 'captcha' },
  { marker: 'recaptcha', vendor: 'Google reCAPTCHA', kind: 'captcha' },
  { marker: 'grecaptcha', vendor: 'Google reCAPTCHA', kind: 'captcha' },
  { marker: 'h-captcha', vendor: 'hCaptcha', kind: 'captcha' },
  { marker: 'hcaptcha', vendor: 'hCaptcha', kind: 'captcha' },
  { marker: 'cf-turnstile', vendor: 'Cloudflare Turnstile', kind: 'captcha' },
  { marker: 'turnstile', vendor: 'Cloudflare Turnstile', kind: 'captcha' },
  { marker: 'challenges.cloudflare.com', vendor: 'Cloudflare Turnstile', kind: 'captcha' },
  { marker: 'cf-challenge', vendor: 'Cloudflare interstitial', kind: 'bot_protection' },
  { marker: 'datadome', vendor: 'DataDome', kind: 'bot_protection' },
  { marker: 'captcha-delivery.com', vendor: 'DataDome', kind: 'bot_protection' },
  { marker: 'px-captcha', vendor: 'PerimeterX / HUMAN', kind: 'bot_protection' },
  { marker: 'perimeterx', vendor: 'PerimeterX / HUMAN', kind: 'bot_protection' },
  { marker: '_pxhd', vendor: 'PerimeterX / HUMAN', kind: 'bot_protection' },
  { marker: 'kasada', vendor: 'Kasada', kind: 'bot_protection' },
  { marker: 'kpsdk', vendor: 'Kasada', kind: 'bot_protection' },
  { marker: 'sensor_data', vendor: 'Akamai Bot Manager', kind: 'bot_protection' },
  { marker: 'akam/', vendor: 'Akamai Bot Manager', kind: 'bot_protection' },
  { marker: 'incapsula', vendor: 'Imperva Incapsula', kind: 'bot_protection' },
];

// ---------------------------------------------------------------------------
// Login walls and paywalls
// ---------------------------------------------------------------------------

export const LOGIN_SELECTORS: string[] = [
  'input[type="password"]',
  'input[name="password"]',
  'form[action*="login"]',
  'form[action*="signin"]',
  'form[action*="sign-in"]',
  'form[action*="authwall"]',
  'a[href*="authwall"]',
  '#login-form',
  '[data-testid="sign-in-form"]',
];

export const LOGIN_TEXT_MARKERS: string[] = [
  'sign in',
  'log in to continue',
  'sign in to continue',
  'sign in to apply',
  'log in to apply',
  'you must be signed in',
  'create an account',
  'create your account',
  'authwall',
  'session has expired',
];

export const PAYWALL_TEXT_MARKERS: string[] = [
  'subscribe to continue',
  'subscription required',
  'become a member to continue',
  'you have reached your article limit',
  'upgrade your plan to continue',
  'this content is for members only',
  'paywall',
];

const PASSWORD_INPUT_PATTERN = /<input\b[^>]*type\s*=\s*["']?password/iu;
const AUTHWALL_URL_PATTERN = /(authwall|\/login|\/signin|\/sign-in|\/account\/login)/iu;

// ---------------------------------------------------------------------------
// Pure detection
// ---------------------------------------------------------------------------

/** Remove scripts, styles and tags so text markers are matched against prose. */
export function stripMarkup(html: string): string {
  return html
    .replace(/<script\b[\s\S]*?<\/script>/giu, ' ')
    .replace(/<style\b[\s\S]*?<\/style>/giu, ' ')
    .replace(/<!--[\s\S]*?-->/gu, ' ')
    .replace(/<[^>]+>/gu, ' ')
    .replace(/&nbsp;/giu, ' ')
    .replace(/&amp;/giu, '&')
    .replace(/&#0?39;|&apos;|&rsquo;|’/giu, "'")
    .replace(/&quot;/giu, '"')
    .replace(/\s+/gu, ' ')
    .trim();
}

function push(findings: GuardFinding[], finding: GuardFinding): void {
  const key = `${finding.kind}:${finding.marker}`;
  if (findings.some((existing) => `${existing.kind}:${existing.marker}` === key)) return;
  findings.push(finding);
}

/**
 * Pure, string-only hard stop detection. No browser, no network, fully unit
 * testable. detectHardStops() delegates all text matching here.
 */
export function detectHardStopsInHtml(html: string): GuardFinding[] {
  const findings: GuardFinding[] = [];
  if (typeof html !== 'string' || html === '') return findings;

  const markup = html.toLowerCase();
  const text = stripMarkup(html).toLowerCase();

  for (const signature of CAPTCHA_MARKUP_SIGNATURES) {
    if (markup.includes(signature.marker)) {
      push(findings, {
        kind: signature.kind,
        marker: signature.marker,
        detail: `${signature.vendor} detected in page markup`,
        source: 'markup',
      });
    }
  }

  for (const marker of CAPTCHA_TEXT_MARKERS) {
    if (text.includes(marker)) {
      push(findings, {
        kind: 'captcha',
        marker,
        detail: 'CAPTCHA or bot check wording on the page',
        source: 'text',
      });
    }
  }

  if (PASSWORD_INPUT_PATTERN.test(html)) {
    push(findings, {
      kind: 'login',
      marker: 'input[type=password]',
      detail: 'The page asks for a password. This assistant never types credentials.',
      source: 'markup',
    });
  }

  for (const marker of LOGIN_TEXT_MARKERS) {
    if (text.includes(marker)) {
      push(findings, {
        kind: 'login',
        marker,
        detail: 'Login or account creation wording on the page',
        source: 'text',
      });
    }
  }

  for (const marker of PAYWALL_TEXT_MARKERS) {
    if (text.includes(marker)) {
      push(findings, {
        kind: 'paywall',
        marker,
        detail: 'Paywalled or gated content',
        source: 'text',
      });
    }
  }

  return findings;
}

// ---------------------------------------------------------------------------
// Live page detection
// ---------------------------------------------------------------------------

/** The slice of a Playwright Page this module needs. Keeps the module testable. */
export interface GuardPage {
  url(): string;
  content(): Promise<string>;
  locator(selector: string): { count(): Promise<number> };
}

function kindForSelector(selector: string): { kind: GuardKind; detail: string } {
  const lower = selector.toLowerCase();
  for (const signature of CAPTCHA_MARKUP_SIGNATURES) {
    if (lower.includes(signature.marker)) {
      return { kind: signature.kind, detail: `${signature.vendor} element present` };
    }
  }
  return { kind: 'captcha', detail: 'CAPTCHA or challenge element present' };
}

/**
 * Live detection against a real page. Selector matching happens here; every
 * text based rule is delegated to detectHardStopsInHtml so the two paths can
 * never drift apart.
 */
export async function detectHardStops(page: GuardPage): Promise<GuardFinding[]> {
  const findings: GuardFinding[] = [];

  for (const selector of CAPTCHA_SELECTORS) {
    let count = 0;
    try {
      count = await page.locator(selector).count();
    } catch {
      continue; // A selector the page engine rejects is not evidence either way.
    }
    if (count > 0) {
      const { kind, detail } = kindForSelector(selector);
      push(findings, { kind, marker: selector, detail, source: 'selector' });
    }
  }

  for (const selector of LOGIN_SELECTORS) {
    let count = 0;
    try {
      count = await page.locator(selector).count();
    } catch {
      continue;
    }
    if (count > 0) {
      push(findings, {
        kind: 'login',
        marker: selector,
        detail: 'Login or account wall element present',
        source: 'selector',
      });
    }
  }

  let url = '';
  try {
    url = page.url();
  } catch {
    url = '';
  }
  if (url !== '' && AUTHWALL_URL_PATTERN.test(url)) {
    push(findings, {
      kind: 'login',
      marker: url,
      detail: 'The browser was redirected to a sign-in URL',
      source: 'url',
    });
  }

  let html = '';
  try {
    html = await page.content();
  } catch {
    html = '';
  }
  for (const finding of detectHardStopsInHtml(html)) push(findings, finding);

  return findings;
}

// ---------------------------------------------------------------------------
// Classification
// ---------------------------------------------------------------------------

/**
 * Map findings onto one of ASSISTANT_ABORT_REASONS (backend/app/api/v1/assistant.py).
 * A paywall is reported as login_required: from the applicant's point of view it
 * is the same gate, and there is no separate server reason for it.
 */
export function classify(findings: GuardFinding[]): string {
  if (findings.some((f) => f.kind === 'captcha')) return 'captcha_detected';
  if (findings.some((f) => f.kind === 'bot_protection')) return 'bot_protection_detected';
  if (findings.some((f) => f.kind === 'login' || f.kind === 'paywall')) return 'login_required';
  if (findings.some((f) => f.kind === 'robots')) return 'robots_disallowed';
  if (findings.some((f) => f.kind === 'question')) return 'unknown_question';
  return 'submission_error';
}

// ---------------------------------------------------------------------------
// robots.txt
// ---------------------------------------------------------------------------

export interface RobotsRule {
  allow: boolean;
  path: string;
}

export interface RobotsRules {
  /** The group that matched, '*' for the wildcard group, '' when nothing matched. */
  matchedAgent: string;
  rules: RobotsRule[];
}

/**
 * Parse robots.txt into the rule group that applies to userAgent. Written by
 * hand on purpose: a dependency here would be a dependency that decides whether
 * we are allowed to touch a site.
 *
 * A group is a run of User-agent lines followed by its rules. The most specific
 * matching group wins; otherwise the '*' group; otherwise no rules at all.
 */
export function parseRobots(text: string, userAgent: string): RobotsRules {
  const wanted = userAgent.trim().toLowerCase();
  const groups: { agents: string[]; rules: RobotsRule[] }[] = [];
  let current: { agents: string[]; rules: RobotsRule[] } | null = null;
  let expectingAgents = false;

  for (const rawLine of (text ?? '').split(/\r?\n/)) {
    const withoutComment = rawLine.split('#')[0] ?? '';
    const line = withoutComment.trim();
    if (line === '') continue;
    const colon = line.indexOf(':');
    if (colon === -1) continue;
    const field = line.slice(0, colon).trim().toLowerCase();
    const value = line.slice(colon + 1).trim();

    if (field === 'user-agent') {
      if (current === null || !expectingAgents) {
        current = { agents: [], rules: [] };
        groups.push(current);
        expectingAgents = true;
      }
      current.agents.push(value.toLowerCase());
      continue;
    }

    if (field === 'allow' || field === 'disallow') {
      if (current === null) continue; // Rules before any User-agent line are ignored.
      expectingAgents = false;
      // "Disallow:" with an empty value means "nothing is disallowed".
      if (field === 'disallow' && value === '') continue;
      if (field === 'allow' && value === '') continue;
      current.rules.push({ allow: field === 'allow', path: value });
    }
  }

  let exact: { agents: string[]; rules: RobotsRule[] } | null = null;
  let exactAgent = '';
  let wildcard: { agents: string[]; rules: RobotsRule[] } | null = null;

  for (const group of groups) {
    for (const agent of group.agents) {
      if (agent === '*') {
        wildcard = wildcard === null ? group : { agents: ['*'], rules: [...wildcard.rules, ...group.rules] };
        continue;
      }
      if (wanted !== '' && (wanted.includes(agent) || agent.includes(wanted))) {
        if (agent.length > exactAgent.length) {
          exact = group;
          exactAgent = agent;
        }
      }
    }
  }

  if (exact !== null) return { matchedAgent: exactAgent, rules: exact.rules };
  if (wildcard !== null) return { matchedAgent: '*', rules: wildcard.rules };
  return { matchedAgent: '', rules: [] };
}

/** robots.txt path matching: '*' is a wildcard, a trailing '$' anchors the end. */
export function robotsPathMatches(pattern: string, pathname: string): boolean {
  if (pattern === '') return false;
  const anchored = pattern.endsWith('$');
  const body = anchored ? pattern.slice(0, -1) : pattern;
  const parts = body.split('*');
  let index = 0;

  for (let i = 0; i < parts.length; i += 1) {
    const part = parts[i] ?? '';
    if (part === '') {
      if (i === 0) continue;
      continue;
    }
    if (i === 0) {
      if (!pathname.startsWith(part)) return false;
      index = part.length;
      continue;
    }
    const found = pathname.indexOf(part, index);
    if (found === -1) return false;
    index = found + part.length;
  }

  if (anchored) {
    const lastPart = parts[parts.length - 1] ?? '';
    if (lastPart === '') return true; // pattern ended with '*$'
    return pathname.length === index;
  }
  return true;
}

/** Longest matching rule wins; on a tie Allow wins, as the major crawlers do. */
export function isPathAllowed(rules: RobotsRules, pathname: string): { allowed: boolean; rule: RobotsRule | null } {
  let best: RobotsRule | null = null;
  for (const rule of rules.rules) {
    if (!robotsPathMatches(rule.path, pathname)) continue;
    if (best === null) {
      best = rule;
      continue;
    }
    if (rule.path.length > best.path.length) {
      best = rule;
    } else if (rule.path.length === best.path.length && rule.allow && !best.allow) {
      best = rule;
    }
  }
  if (best === null) return { allowed: true, rule: null };
  return { allowed: best.allow, rule: best };
}

export interface RobotsVerdict {
  allowed: boolean;
  reason: string;
}

export interface FetchLikeResponse {
  status: number;
  ok: boolean;
  text(): Promise<string>;
}

export type FetchLike = (url: string, init?: { headers?: Record<string, string> }) => Promise<FetchLikeResponse>;

/**
 * Fetch and evaluate robots.txt for a URL.
 *
 * Fails OPEN only on 404, which means the site publishes no robots.txt and has
 * therefore expressed no restriction. Every other outcome - a matching Disallow,
 * a 403, a 500, a network error, an unparseable response - fails CLOSED. If we
 * cannot read the rules, we do not get to assume they are in our favour.
 */
export async function robotsAllows(
  url: string,
  userAgent: string,
  fetchImpl?: FetchLike,
): Promise<RobotsVerdict> {
  let target: URL;
  try {
    target = new URL(url);
  } catch {
    return { allowed: false, reason: `Not a valid URL: ${url}` };
  }

  const doFetch: FetchLike =
    fetchImpl ??
    ((robotsUrl, init) => fetch(robotsUrl, { headers: init?.headers, signal: AbortSignal.timeout(15_000) }));

  const robotsUrl = `${target.origin}/robots.txt`;
  let response: FetchLikeResponse;
  try {
    response = await doFetch(robotsUrl, { headers: { 'User-Agent': userAgent, Accept: 'text/plain' } });
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    return { allowed: false, reason: `Could not read ${robotsUrl} (${message}); refusing to proceed.` };
  }

  if (response.status === 404) {
    return { allowed: true, reason: 'No robots.txt published (404); no restriction expressed.' };
  }
  if (!response.ok) {
    return {
      allowed: false,
      reason: `${robotsUrl} returned HTTP ${response.status}; rules unreadable, refusing to proceed.`,
    };
  }

  let body: string;
  try {
    body = await response.text();
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    return { allowed: false, reason: `Could not read the body of ${robotsUrl} (${message}).` };
  }

  const rules = parseRobots(body, userAgent);
  const pathname = `${target.pathname}${target.search}`;
  const verdict = isPathAllowed(rules, pathname);
  if (verdict.allowed) {
    return {
      allowed: true,
      reason: verdict.rule
        ? `Allowed by "Allow: ${verdict.rule.path}" for user-agent ${rules.matchedAgent || '*'}.`
        : `No rule in robots.txt matches ${pathname}.`,
    };
  }
  return {
    allowed: false,
    reason: `robots.txt disallows ${pathname} via "Disallow: ${verdict.rule?.path ?? '/'}" for user-agent ${
      rules.matchedAgent || '*'
    }.`,
  };
}

/** Convenience: turn a robots verdict into a finding for the review task. */
export function robotsFinding(url: string, reason: string): GuardFinding {
  return { kind: 'robots', marker: url, detail: reason, source: 'robots' };
}
