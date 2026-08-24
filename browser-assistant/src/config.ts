/**
 * Environment parsing and validation for the local browser assistant.
 *
 * Two rules are enforced here rather than deeper in the code, because a bad
 * value must stop the program before a browser ever opens:
 *
 *   1. HEADLESS=true is refused. The assistant is only allowed to act on your
 *      behalf while you can see it acting.
 *   2. A missing token is refused. The assistant has no other way to prove it
 *      is you, and it must never fall back to an unauthenticated mode.
 */

import { existsSync, readFileSync } from 'node:fs';
import path from 'node:path';

export const ASSISTANT_NAME = 'jobagent-browser-assistant';
export const ASSISTANT_VERSION = '1.0.0';

/** Raised for any configuration problem. Always carries an actionable message. */
export class ConfigError extends Error {
  constructor(message: string) {
    super(message);
    this.name = 'ConfigError';
  }
}

export interface AssistantConfig {
  apiBaseUrl: string;
  token: string;
  pollIntervalSeconds: number;
  headless: false;
  slowMoMs: number;
  maxRuntimeSeconds: number;
  screenshotOnSuccess: boolean;
  userAgentSuffix: string;
  /** Product token used when matching robots.txt User-agent groups. */
  robotsUserAgent: string;
  dryRun: boolean;
  assistantVersion: string;
}

export type EnvLike = Record<string, string | undefined>;

const TRUE_VALUES = new Set(['1', 'true', 'yes', 'y', 'on']);
const FALSE_VALUES = new Set(['0', 'false', 'no', 'n', 'off']);

/**
 * Minimal .env reader. No dependency, no interpolation, no surprises: KEY=VALUE
 * lines, '#' comments, optional surrounding quotes. Values already present in
 * the real environment always win.
 */
export function loadDotEnv(file = path.resolve(process.cwd(), '.env'), env: EnvLike = process.env): void {
  if (!existsSync(file)) return;
  const raw = readFileSync(file, 'utf8');
  for (const line of raw.split(/\r?\n/)) {
    const trimmed = line.trim();
    if (trimmed === '' || trimmed.startsWith('#')) continue;
    const eq = trimmed.indexOf('=');
    if (eq === -1) continue;
    const key = trimmed.slice(0, eq).trim();
    if (key === '' || env[key] !== undefined) continue;
    let value = trimmed.slice(eq + 1).trim();
    if (
      (value.startsWith('"') && value.endsWith('"') && value.length >= 2) ||
      (value.startsWith("'") && value.endsWith("'") && value.length >= 2)
    ) {
      value = value.slice(1, -1);
    }
    env[key] = value;
  }
}

function readString(env: EnvLike, key: string, fallback: string): string {
  const value = env[key];
  if (value === undefined) return fallback;
  const trimmed = value.trim();
  return trimmed === '' ? fallback : trimmed;
}

export function parseBoolean(raw: string | undefined, fallback: boolean, key: string): boolean {
  if (raw === undefined || raw.trim() === '') return fallback;
  const value = raw.trim().toLowerCase();
  if (TRUE_VALUES.has(value)) return true;
  if (FALSE_VALUES.has(value)) return false;
  throw new ConfigError(`${key} must be true or false, got "${raw}".`);
}

export function parseInteger(
  raw: string | undefined,
  fallback: number,
  key: string,
  min: number,
  max: number,
): number {
  if (raw === undefined || raw.trim() === '') return fallback;
  const value = Number(raw.trim());
  if (!Number.isFinite(value) || !Number.isInteger(value)) {
    throw new ConfigError(`${key} must be a whole number, got "${raw}".`);
  }
  if (value < min || value > max) {
    throw new ConfigError(`${key} must be between ${min} and ${max}, got ${value}.`);
  }
  return value;
}

/** Product token for robots.txt matching, derived from the user agent suffix. */
export function robotsTokenFrom(suffix: string): string {
  const firstWord = suffix.trim().split(/[\s/]+/u)[0] ?? '';
  return firstWord === '' ? 'JobAgentBrowserAssistant' : firstWord;
}

export function loadConfig(env: EnvLike = process.env): AssistantConfig {
  const apiBaseUrl = readString(env, 'API_BASE_URL', 'http://localhost:8000/api/v1').replace(/\/+$/u, '');
  try {
    // eslint-disable-next-line no-new
    new URL(apiBaseUrl);
  } catch {
    throw new ConfigError(`API_BASE_URL is not a valid URL: "${apiBaseUrl}".`);
  }

  const token = readString(env, 'BROWSER_ASSISTANT_TOKEN', '');
  if (token === '') {
    throw new ConfigError(
      'BROWSER_ASSISTANT_TOKEN is missing. Copy .env.example to .env and paste the ' +
        'token from your backend configuration (ASSISTANT_TOKEN). The assistant will ' +
        'not run unauthenticated.',
    );
  }

  const headless = parseBoolean(env['HEADLESS'], false, 'HEADLESS');
  if (headless) {
    throw new ConfigError(
      'HEADLESS=true is refused. This assistant only runs in a VISIBLE browser so you ' +
        'can watch every action, take over, or close the window at any time. Running ' +
        'hidden is also the first step toward evading bot detection, which this program ' +
        'will not do. Set HEADLESS=false (or remove the line) and start again.',
    );
  }

  const userAgentSuffix = readString(
    env,
    'ASSISTANT_USER_AGENT_SUFFIX',
    'JobAgentBrowserAssistant/1.0 (+local; headed; human-supervised)',
  );

  return {
    apiBaseUrl,
    token,
    pollIntervalSeconds: parseInteger(env['POLL_INTERVAL_SECONDS'], 20, 'POLL_INTERVAL_SECONDS', 1, 3600),
    headless: false,
    slowMoMs: parseInteger(env['SLOW_MO_MS'], 120, 'SLOW_MO_MS', 0, 5000),
    maxRuntimeSeconds: parseInteger(env['MAX_RUNTIME_SECONDS'], 180, 'MAX_RUNTIME_SECONDS', 30, 3600),
    screenshotOnSuccess: parseBoolean(env['SCREENSHOT_ON_SUCCESS'], true, 'SCREENSHOT_ON_SUCCESS'),
    userAgentSuffix,
    robotsUserAgent: robotsTokenFrom(userAgentSuffix),
    dryRun: parseBoolean(env['DRY_RUN'], true, 'DRY_RUN'),
    assistantVersion: ASSISTANT_VERSION,
  };
}
