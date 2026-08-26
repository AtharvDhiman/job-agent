import { describe, expect, it } from 'vitest';

import {
  BROWSER_SUPPORTED_CONNECTORS,
  CAPTCHA_SELECTORS,
  CAPTCHA_TEXT_MARKERS,
  LOGIN_SELECTORS,
  LOGIN_TEXT_MARKERS,
  NEVER_DO,
  PAYWALL_TEXT_MARKERS,
  PROHIBITED_CONNECTORS,
  classify,
  connectorIsProhibited,
  detectHardStopsInHtml,
  isPathAllowed,
  mayClickSubmit,
  parseRobots,
  robotsAllows,
  robotsPathMatches,
  taskIsAllowed,
  urlIsProhibited,
  type FetchLike,
  type FetchLikeResponse,
} from './guards.js';

const CLEAN_PAGE = `
<html><head><title>Apply - Senior Engineer</title></head>
<body>
  <h1>Apply for Senior Engineer</h1>
  <form action="/apply" method="post">
    <label for="full_name">Full name *</label>
    <input id="full_name" name="full_name" type="text" required />
    <label for="email">Email *</label>
    <input id="email" name="email" type="email" required />
    <label for="resume">Resume *</label>
    <input id="resume" name="resume" type="file" required />
    <label for="start">Earliest start date</label>
    <input id="start" name="start" type="date" />
    <button type="submit">Submit application</button>
  </form>
</body></html>
`;

/** One realistic snippet per vendor the guards claim to cover. */
const CAPTCHA_SAMPLES: { vendor: string; html: string }[] = [
  {
    vendor: 'reCAPTCHA v2',
    html: '<div class="g-recaptcha" data-sitekey="abc"></div>',
  },
  {
    vendor: 'reCAPTCHA iframe',
    html: '<iframe src="https://www.google.com/recaptcha/api2/anchor?k=abc"></iframe>',
  },
  {
    vendor: 'hCaptcha',
    html: '<div class="h-captcha" data-sitekey="abc"></div><script src="https://js.hcaptcha.com/1/api.js"></script>',
  },
  {
    vendor: 'Cloudflare Turnstile',
    html: '<div class="cf-turnstile" data-sitekey="abc"></div>',
  },
  {
    vendor: 'Cloudflare interstitial',
    html: '<div id="cf-challenge-running">Checking your browser before accessing the site.</div>',
  },
  {
    vendor: 'DataDome',
    html: '<script src="https://js.datadome.co/tags.js"></script>',
  },
  {
    vendor: 'PerimeterX',
    html: '<div id="px-captcha"></div>',
  },
  {
    vendor: 'Kasada',
    html: '<script src="https://cdn.example.com/kpsdk-cd.js"></script>',
  },
  {
    vendor: 'Akamai',
    html: '<form><input type="hidden" name="sensor_data" value="7a;..." /></form>',
  },
];

describe('detectHardStopsInHtml', () => {
  it('finds a hard stop for every CAPTCHA and bot protection vendor', () => {
    for (const sample of CAPTCHA_SAMPLES) {
      const findings = detectHardStopsInHtml(sample.html);
      expect(findings.length, `${sample.vendor} produced no finding`).toBeGreaterThan(0);
      expect(
        findings.some((finding) => finding.kind === 'captcha' || finding.kind === 'bot_protection'),
        `${sample.vendor} was not classified as a challenge`,
      ).toBe(true);
    }
  });

  it('finds every declared CAPTCHA text marker', () => {
    for (const marker of CAPTCHA_TEXT_MARKERS) {
      const findings = detectHardStopsInHtml(`<html><body><p>${marker}</p></body></html>`);
      expect(findings.some((finding) => finding.marker === marker), marker).toBe(true);
    }
  });

  it('detects a login wall from a password field', () => {
    const findings = detectHardStopsInHtml(
      '<form action="/session"><input type="text" name="user"><input type="password" name="pw"></form>',
    );
    expect(findings.some((finding) => finding.kind === 'login')).toBe(true);
    expect(classify(findings)).toBe('login_required');
  });

  it('detects a login wall from wording alone', () => {
    const findings = detectHardStopsInHtml('<html><body><h1>Log in to continue</h1></body></html>');
    expect(findings.some((finding) => finding.kind === 'login')).toBe(true);
  });

  it('detects a paywall', () => {
    const findings = detectHardStopsInHtml('<html><body><p>Subscription required to view this role.</p></body></html>');
    expect(findings.some((finding) => finding.kind === 'paywall')).toBe(true);
    expect(classify(findings)).toBe('login_required');
  });

  it('reports nothing for a clean application form', () => {
    expect(detectHardStopsInHtml(CLEAN_PAGE)).toEqual([]);
  });

  it('ignores empty input', () => {
    expect(detectHardStopsInHtml('')).toEqual([]);
  });

  it('does not double report the same marker', () => {
    const findings = detectHardStopsInHtml(
      '<div class="g-recaptcha"></div><div class="g-recaptcha"></div>',
    );
    const recaptcha = findings.filter((finding) => finding.marker === 'g-recaptcha');
    expect(recaptcha).toHaveLength(1);
  });
});

describe('exported selector lists', () => {
  it('covers the vendors named in docs/COMPLIANCE.md section 3', () => {
    const joined = CAPTCHA_SELECTORS.join(' ');
    expect(joined).toContain('.g-recaptcha');
    expect(joined).toContain('iframe[src*="recaptcha"]');
    expect(joined).toContain('.h-captcha');
    expect(joined).toContain('.cf-turnstile');
    expect(joined).toContain('#px-captcha');
    expect(joined).toContain('datadome');
    expect(joined).toContain('kasada');
    expect(joined).toContain('sensor_data');
    expect(LOGIN_SELECTORS.join(' ')).toContain('input[type="password"]');
    expect(LOGIN_TEXT_MARKERS).toContain('authwall');
    expect(PAYWALL_TEXT_MARKERS.length).toBeGreaterThan(0);
  });

  it('publishes the refusal list', () => {
    expect(NEVER_DO.length).toBeGreaterThan(5);
    expect(NEVER_DO.join(' ').toLowerCase()).toContain('captcha');
    expect(NEVER_DO.join(' ').toLowerCase()).toContain('navigator.webdriver');
    expect(NEVER_DO.join(' ').toLowerCase()).toContain('linkedin');
  });
});

describe('platforms this assistant never automates', () => {
  const base = { connector_key: 'greenhouse', apply_url: 'https://boards.greenhouse.io/x/jobs/1', mode: 'auto_submit', may_click_submit: true };

  it('mirrors the backend hard-prohibited list', () => {
    // HARD_PROHIBITED_PLATFORMS in backend/app/services/policy.py
    expect([...PROHIBITED_CONNECTORS].sort()).toEqual(['indeed', 'linkedin']);
  });

  it.each(['linkedin', 'LinkedIn', ' INDEED '])('refuses connector %s', (key) => {
    expect(connectorIsProhibited(key)).toBe(true);
    const verdict = taskIsAllowed({ ...base, connector_key: key });
    expect(verdict.allowed).toBe(false);
    expect(verdict.reason.toLowerCase()).toContain('prohibits automated applying');
  });

  it('refuses a prohibited task even when the server authorized the click', () => {
    // The whole point: a wrong answer upstream must not reach the browser.
    expect(
      mayClickSubmit({ ...base, connector_key: 'linkedin', may_click_submit: true }),
    ).toBe(false);
  });

  it.each([
    'https://www.linkedin.com/jobs/view/12345',
    'https://linkedin.com/jobs/view/1',
    'https://uk.indeed.com/viewjob?jk=abc',
    'https://www.indeed.com/applystart?jk=abc',
  ])('refuses %s by hostname even when the connector key looks innocent', (url) => {
    expect(urlIsProhibited(url)).toBe(true);
    expect(taskIsAllowed({ ...base, connector_key: 'careers_page', apply_url: url }).allowed).toBe(false);
  });

  it.each([
    'https://boards.greenhouse.io/example/jobs/1',
    'https://jobs.lever.co/example/1',
    'https://notlinkedin.com/jobs/1',
    'https://careers.example.com/linkedin-engineer',
  ])('allows %s', (url) => {
    expect(urlIsProhibited(url)).toBe(false);
    expect(taskIsAllowed({ ...base, apply_url: url }).allowed).toBe(true);
  });

  it('allows an ordinary board', () => {
    expect(connectorIsProhibited('greenhouse')).toBe(false);
    expect(taskIsAllowed(base).allowed).toBe(true);
  });
});

describe('platforms this assistant knows how to drive', () => {
  const base = { connector_key: 'greenhouse', apply_url: 'https://boards.greenhouse.io/x/jobs/1', mode: 'auto_submit', may_click_submit: true };

  it('mirrors the registry allow-list in the backend', () => {
    // registry.browser_submission_keys() in backend/app/connectors/base.py.
    // backend/tests/unit/test_compliance.py parses this array and asserts they match.
    expect([...BROWSER_SUPPORTED_CONNECTORS].sort()).toEqual([
      'ashby',
      'greenhouse',
      'lever',
      'smartrecruiters',
      'workable',
    ]);
  });

  it.each(['greenhouse', 'lever', 'ashby', 'workable', 'smartrecruiters'])('allows %s', (key) => {
    const verdict = taskIsAllowed({ ...base, connector_key: key, apply_url: 'https://jobs.example.com/apply/1' });
    expect(verdict.allowed).toBe(true);
    expect(verdict.reason).toContain('allow-list');
  });

  it.each(['adzuna', 'rss', 'careers_page', 'manual', 'wellfound', ''])(
    'refuses discovery-only connector %s',
    (key) => {
      const verdict = taskIsAllowed({ ...base, connector_key: key, apply_url: 'https://jobs.example.com/apply/1' });
      expect(verdict.allowed).toBe(false);
      expect(verdict.reason).toContain('discovery and review only');
    },
  );

  it('refuses an unsupported connector even when the server authorized the click', () => {
    // A widened allow-list on the server must not widen this process.
    expect(
      mayClickSubmit({ ...base, connector_key: 'rss', may_click_submit: true, mode: 'auto_submit' }),
    ).toBe(false);
  });

  it.each(['GreenHouse', ' lever ', 'ASHBY'])('matches %s case-insensitively', (key) => {
    expect(taskIsAllowed({ ...base, connector_key: key, apply_url: 'https://jobs.example.com/apply/1' }).allowed).toBe(true);
  });

  it('never contains a prohibited platform', () => {
    for (const key of PROHIBITED_CONNECTORS) {
      expect(BROWSER_SUPPORTED_CONNECTORS).not.toContain(key);
    }
  });
});

describe('mayClickSubmit', () => {
  const base = { connector_key: 'greenhouse', apply_url: 'https://boards.greenhouse.io/x/jobs/1', mode: 'auto_submit', may_click_submit: true };

  it('needs the server flag AND the auto_submit mode to agree', () => {
    expect(mayClickSubmit(base)).toBe(true);
    expect(mayClickSubmit({ ...base, may_click_submit: false })).toBe(false);
    expect(mayClickSubmit({ ...base, mode: 'assisted_autofill' })).toBe(false);
    expect(mayClickSubmit({ ...base, mode: 'assisted_autofill', may_click_submit: true })).toBe(false);
  });
});

describe('classify', () => {
  it('maps each finding kind onto a backend abort reason', () => {
    expect(classify([{ kind: 'captcha', marker: 'x', detail: '', source: 'markup' }])).toBe('captcha_detected');
    expect(classify([{ kind: 'bot_protection', marker: 'x', detail: '', source: 'markup' }])).toBe(
      'bot_protection_detected',
    );
    expect(classify([{ kind: 'login', marker: 'x', detail: '', source: 'text' }])).toBe('login_required');
    expect(classify([{ kind: 'paywall', marker: 'x', detail: '', source: 'text' }])).toBe('login_required');
    expect(classify([{ kind: 'robots', marker: 'x', detail: '', source: 'robots' }])).toBe('robots_disallowed');
    expect(classify([{ kind: 'question', marker: 'x', detail: '', source: 'server' }])).toBe('unknown_question');
    expect(classify([])).toBe('submission_error');
  });

  it('prefers the most specific reason when several fire at once', () => {
    const findings = [
      { kind: 'login' as const, marker: 'a', detail: '', source: 'text' as const },
      { kind: 'bot_protection' as const, marker: 'b', detail: '', source: 'markup' as const },
      { kind: 'captcha' as const, marker: 'c', detail: '', source: 'markup' as const },
    ];
    expect(classify(findings)).toBe('captcha_detected');
  });

  it('only ever returns a key the backend accepts', () => {
    // Mirrors ASSISTANT_ABORT_REASONS in backend/app/api/v1/assistant.py.
    const accepted = new Set([
      'captcha_detected',
      'login_required',
      'bot_protection_detected',
      'robots_disallowed',
      'unknown_question',
      'free_text_question',
      'missing_attachment',
      'validation_failed',
      'unsupported_platform',
      'submission_error',
    ]);
    const kinds = ['captcha', 'bot_protection', 'login', 'paywall', 'robots', 'question'] as const;
    for (const kind of kinds) {
      expect(accepted.has(classify([{ kind, marker: 'x', detail: '', source: 'markup' }]))).toBe(true);
    }
  });
});

describe('robots path matching', () => {
  it('handles prefixes, wildcards and end anchors', () => {
    expect(robotsPathMatches('/careers/', '/careers/apply')).toBe(true);
    expect(robotsPathMatches('/careers/', '/jobs/apply')).toBe(false);
    expect(robotsPathMatches('/', '/anything')).toBe(true);
    expect(robotsPathMatches('/*/apply', '/careers/apply')).toBe(true);
    expect(robotsPathMatches('/private$', '/private')).toBe(true);
    expect(robotsPathMatches('/private$', '/private/page')).toBe(false);
  });
});

describe('parseRobots', () => {
  it('reads the wildcard group when no specific group matches', () => {
    const rules = parseRobots('User-agent: *\nDisallow: /admin\nAllow: /admin/public\n', 'JobAgentBrowserAssistant');
    expect(rules.matchedAgent).toBe('*');
    expect(rules.rules).toEqual([
      { allow: false, path: '/admin' },
      { allow: true, path: '/admin/public' },
    ]);
  });

  it('prefers a group naming our user agent', () => {
    const text = [
      'User-agent: *',
      'Disallow: /admin',
      '',
      'User-agent: JobAgentBrowserAssistant',
      'Disallow: /careers/',
      '',
    ].join('\n');
    const rules = parseRobots(text, 'JobAgentBrowserAssistant');
    expect(rules.matchedAgent).toBe('jobagentbrowserassistant');
    expect(rules.rules).toEqual([{ allow: false, path: '/careers/' }]);
  });

  it('ignores comments and an empty Disallow', () => {
    const rules = parseRobots('User-agent: *  # everyone\nDisallow:\n', 'anything');
    expect(rules.rules).toEqual([]);
    expect(isPathAllowed(rules, '/careers/apply').allowed).toBe(true);
  });

  it('gives the longest matching rule precedence', () => {
    const rules = parseRobots('User-agent: *\nDisallow: /careers/\nAllow: /careers/apply\n', 'bot');
    expect(isPathAllowed(rules, '/careers/apply/12345').allowed).toBe(true);
    expect(isPathAllowed(rules, '/careers/internal').allowed).toBe(false);
  });

  it('lets Allow win a tie', () => {
    const rules = parseRobots('User-agent: *\nDisallow: /careers\nAllow: /careers\n', 'bot');
    expect(isPathAllowed(rules, '/careers').allowed).toBe(true);
  });
});

function stubFetch(status: number, body: string): FetchLike {
  return async (): Promise<FetchLikeResponse> => ({
    status,
    ok: status >= 200 && status < 300,
    text: async () => body,
  });
}

describe('robotsAllows', () => {
  const agent = 'JobAgentBrowserAssistant';

  it('allows a path no rule covers', async () => {
    const verdict = await robotsAllows(
      'https://boards.example.com/careers/apply/42',
      agent,
      stubFetch(200, 'User-agent: *\nDisallow: /admin\n'),
    );
    expect(verdict.allowed).toBe(true);
  });

  it('fails OPEN when there is no robots.txt at all (404)', async () => {
    const verdict = await robotsAllows('https://example.com/apply', agent, stubFetch(404, 'Not found'));
    expect(verdict.allowed).toBe(true);
    expect(verdict.reason).toContain('404');
  });

  it('fails CLOSED on a blanket Disallow: /', async () => {
    const verdict = await robotsAllows(
      'https://example.com/careers/apply',
      agent,
      stubFetch(200, 'User-agent: *\nDisallow: /\n'),
    );
    expect(verdict.allowed).toBe(false);
    expect(verdict.reason).toContain('disallows');
  });

  it('fails CLOSED when robots.txt cannot be fetched', async () => {
    const verdict = await robotsAllows('https://example.com/apply', agent, async () => {
      throw new Error('ECONNREFUSED');
    });
    expect(verdict.allowed).toBe(false);
    expect(verdict.reason).toContain('ECONNREFUSED');
  });

  it('fails CLOSED on a server error', async () => {
    const verdict = await robotsAllows('https://example.com/apply', agent, stubFetch(503, ''));
    expect(verdict.allowed).toBe(false);
    expect(verdict.reason).toContain('503');
  });

  it('honours a group that names this assistant specifically', async () => {
    const body = ['User-agent: *', 'Allow: /', '', `User-agent: ${agent}`, 'Disallow: /careers/'].join('\n');
    const verdict = await robotsAllows('https://example.com/careers/apply', agent, stubFetch(200, body));
    expect(verdict.allowed).toBe(false);
  });

  it('refuses an unparseable URL', async () => {
    const verdict = await robotsAllows('not-a-url', agent, stubFetch(404, ''));
    expect(verdict.allowed).toBe(false);
  });
});
