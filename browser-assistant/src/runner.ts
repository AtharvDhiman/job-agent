/**
 * The lifecycle of one attempt.
 *
 * health -> claim -> robots -> open a VISIBLE browser -> guard -> discover ->
 * ask the server -> attach -> fill -> guard again -> hand off or submit.
 *
 * Every branch that stops has exactly one shape: report the abort with a reason
 * drawn from ASSISTANT_ABORT_REASONS, close the browser, move on. A policy abort
 * is never retried; the server has already turned it into a review task with a
 * link and the prefilled draft.
 */

import { mkdtemp, writeFile } from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';

import type { Browser, BrowserContext, Page } from 'playwright';
import { chromium } from 'playwright';

import type { AssistantConfig } from './config.js';
import type { AssistantApi, ResultIn, ServerAnswer, TaskOut } from './api.js';
import { describe } from './api.js';
import { adapterForUrl, checkFormSafety } from './platforms/index.js';
import {
  classify,
  detectHardStops,
  mayClickSubmit,
  robotsAllows,
  robotsFinding,
  taskIsAllowed,
  type GuardFinding,
} from './core/guards.js';
import {
  createPlaywrightAdapter,
  fillAll,
  requiredFailures,
  type FieldSpec,
  type FillResult,
} from './core/fill.js';
import { discoverQuestions, type DiscoveredQuestion } from './core/discover.js';

export type RunOutcome =
  | 'paused'
  | 'idle'
  | 'submitted'
  | 'aborted'
  | 'failed'
  | 'handoff'
  | 'dry_run';

export interface RunnerContext {
  config: AssistantConfig;
  api: AssistantApi;
  log: (message: string) => void;
}

interface Attempt {
  browser: Browser | null;
  context: BrowserContext | null;
  page: Page | null;
  /** Set for the assisted hand-off and for dry runs: the window stays open. */
  keepOpen: boolean;
}

const CONFIRMATION_PATTERNS: RegExp[] = [
  /confirmation\s*(?:number|code|id|#)\s*[:#-]?\s*([A-Za-z0-9][A-Za-z0-9._-]{3,})/iu,
  /application\s*(?:number|id|reference)\s*[:#-]?\s*([A-Za-z0-9][A-Za-z0-9._-]{3,})/iu,
  /reference\s*(?:number|code|id)\s*[:#-]?\s*([A-Za-z0-9][A-Za-z0-9._-]{3,})/iu,
  /\b((?:REQ|APP|JOB)-[A-Za-z0-9]{4,})\b/u,
];

const SUBMIT_SELECTORS: string[] = [
  'button[type="submit"]',
  'input[type="submit"]',
  '#submit_app',
  '#btn-submit',
  'button:has-text("Submit application")',
  'button:has-text("Submit Application")',
  'button:has-text("Submit")',
  'button:has-text("Send application")',
];

export async function runOnce(ctx: RunnerContext): Promise<RunOutcome> {
  const { api, config, log } = ctx;

  const health = await api.health();
  if (!health.global_automation_enabled) {
    log('  Automation kill-switch is OFF on the server. Nothing will be filled. Still polling.');
    return 'paused';
  }

  const task = await api.nextTask('any');
  if (task === null) {
    return 'idle';
  }

  log('');
  log(`  Task: ${task.job_title} at ${task.company} (${task.connector_key})`);
  log(`  Mode: ${task.mode}   may_click_submit=${task.may_click_submit}`);
  log(`  URL:  ${task.apply_url}`);

  const attempt: Attempt = { browser: null, context: null, page: null, keepOpen: false };
  const deadlineMs = config.maxRuntimeSeconds * 1000;

  try {
    return await withTimeout(runTask(ctx, task, attempt), deadlineMs, config.maxRuntimeSeconds);
  } catch (error) {
    log(`  ERROR: ${describe(error)}`);
    await closeQuietly(attempt);
    try {
      await api.reportResult(task.application_id, {
        outcome: 'failed',
        abort_reason: 'submission_error',
        error_message: describe(error).slice(0, 500),
      });
      log('  Reported as failed. The server has opened a review task; this attempt is not retried.');
    } catch (reportError) {
      log(`  Could not report the failure: ${describe(reportError)}`);
    }
    return 'failed';
  } finally {
    if (!attempt.keepOpen) await closeQuietly(attempt);
  }
}

async function runTask(ctx: RunnerContext, task: TaskOut, attempt: Attempt): Promise<RunOutcome> {
  const { api, config, log } = ctx;

  // ---- 2. platforms we never automate -------------------------------------
  // Checked here, in the process that would do the visiting, and before the
  // browser is even launched. The server has its own gate; this one does not
  // depend on the server being right.
  const platform = taskIsAllowed(task);
  if (!platform.allowed) {
    log(`  HARD STOP: ${platform.reason}`);
    return abort(
      ctx,
      task,
      'unsupported_platform',
      [{ kind: 'robots', marker: task.connector_key, detail: platform.reason, source: 'server' }],
      null,
    );
  }

  // ---- 3. robots.txt ------------------------------------------------------
  const verdict = await robotsAllows(task.apply_url, config.robotsUserAgent);
  log(`  robots.txt: ${verdict.allowed ? 'allowed' : 'DISALLOWED'} - ${verdict.reason}`);
  if (!verdict.allowed) {
    return abort(ctx, task, 'robots_disallowed', [robotsFinding(task.apply_url, verdict.reason)], null);
  }

  // ---- 4. visible browser -------------------------------------------------
  const browser = await chromium.launch({ headless: false, slowMo: config.slowMoMs });
  attempt.browser = browser;

  const probeContext = await browser.newContext();
  const probePage = await probeContext.newPage();
  const realUserAgent = await probePage.evaluate(() => navigator.userAgent);
  await probeContext.close();

  const context = await browser.newContext({
    viewport: { width: 1440, height: 900 },
    // Append, never replace. The site is told exactly who is visiting.
    userAgent: `${realUserAgent} ${config.userAgentSuffix}`.trim(),
  });
  attempt.context = context;
  const page = await context.newPage();
  attempt.page = page;
  log(`  User-Agent: ${realUserAgent} ${config.userAgentSuffix}`);

  // ---- 5. open and guard --------------------------------------------------
  await page.goto(task.apply_url, { waitUntil: 'domcontentloaded', timeout: 45_000 });
  await page.waitForLoadState('networkidle', { timeout: 20_000 }).catch(() => {
    log('  (network never went idle; continuing with what has loaded)');
  });

  // The portal adapter is the per-ATS half of the check: checkFormSafety runs
  // detectHardStops itself first, then additionally proves we are actually on
  // the application form this ATS is meant to serve, and refuses gates the
  // generic markers do not recognise. With no adapter (a portal we have not
  // built support for) we still run the generic pass, because discovering a job
  // never implies its form is safe to fill.
  const portal = adapterForUrl(task.apply_url);
  if (portal) {
    log(`  Portal adapter: ${portal.displayName}`);
    const safety = await checkFormSafety(page, portal);
    if (!safety.safe) {
      log(`  HARD STOP before filling: ${safety.reason}`);
      for (const finding of safety.findings) {
        log(`    - [${finding.kind}] ${finding.marker}: ${finding.detail}`);
      }
      return abort(ctx, task, safety.reason, safety.findings, page);
    }
  } else {
    log('  No portal adapter for this host; generic guards only.');
    const beforeFindings = await detectHardStops(page);
    if (beforeFindings.length > 0) {
      const reason = classify(beforeFindings);
      log(`  HARD STOP before filling: ${reason}`);
      for (const finding of beforeFindings) {
        log(`    - [${finding.kind}] ${finding.marker}: ${finding.detail}`);
      }
      return abort(ctx, task, reason, beforeFindings, page);
    }
  }

  // ---- 6. discover and ask the server ------------------------------------
  const discovered = await discoverQuestions(page);
  log(`  Discovered ${discovered.length} form question(s).`);
  const questionsOut = await api.resolveQuestions(
    task.application_id,
    discovered.map((question) => ({
      external_id: question.external_id,
      text: question.text,
      type: question.type,
      required: question.required,
      options: question.options,
    })),
  );

  if (questionsOut.must_abort) {
    const blocking = questionsOut.unanswerable.filter((item) => item.required);
    const typeById = new Map(discovered.map((question) => [question.external_id, question.type]));
    const allFreeText =
      blocking.length > 0 && blocking.every((item) => typeById.get(item.external_id) === 'long_text');
    const reason = allFreeText ? 'free_text_question' : 'unknown_question';
    const findings: GuardFinding[] = questionsOut.unanswerable.map((item) => ({
      kind: 'question',
      marker: item.external_id,
      detail: `${item.text} (${item.reason || 'no verified fact covers this'})`,
      source: 'server',
    }));
    log(`  HARD STOP: the server cannot answer ${blocking.length} required question(s) truthfully.`);
    for (const item of blocking) log(`    - ${item.text}`);
    return abort(ctx, task, reason, findings, page);
  }

  // ---- 7. attachments -----------------------------------------------------
  const specs = buildFieldSpecs(task, discovered, questionsOut.answers);
  let filesByField: Record<string, string[]> = {};
  try {
    filesByField = await downloadAttachments(ctx, task, specs, discovered);
  } catch (error) {
    log(`  Attachment download failed: ${describe(error)}`);
    return abort(
      ctx,
      task,
      'missing_attachment',
      [{ kind: 'question', marker: 'attachment', detail: describe(error), source: 'server' }],
      page,
    );
  }

  const missingRequiredFile = specs.find(
    (spec) => spec.required && spec.type === 'file' && (filesByField[spec.question_external_id] ?? []).length === 0,
  );
  if (missingRequiredFile !== undefined) {
    log(`  HARD STOP: required attachment for "${missingRequiredFile.label}" is not available.`);
    return abort(
      ctx,
      task,
      'missing_attachment',
      [
        {
          kind: 'question',
          marker: missingRequiredFile.question_external_id,
          detail: `No document supplied for required upload "${missingRequiredFile.label}"`,
          source: 'server',
        },
      ],
      page,
    );
  }

  // ---- 8. fill and guard again -------------------------------------------
  const adapter = createPlaywrightAdapter(page);
  const results = await fillAll(adapter, specs, { filesByField });
  for (const result of results) {
    log(`    ${result.ok ? 'ok  ' : 'skip'} ${result.name} (${result.reason})`);
  }

  const failures = requiredFailures(specs, results);
  if (failures.length > 0) {
    log('  HARD STOP: a required field could not be filled. The assistant does not guess.');
    for (const failure of failures) log(`    - ${failure.name}: ${failure.reason}`);
    return abort(
      ctx,
      task,
      'validation_failed',
      failures.map((failure) => ({
        kind: 'question' as const,
        marker: failure.name,
        detail: `${failure.reason} (tried ${failure.selector})`,
        source: 'server' as const,
      })),
      page,
      results,
    );
  }

  // Sites inject challenges after interaction, so the page is checked again.
  const afterFindings = await detectHardStops(page);
  if (afterFindings.length > 0) {
    const reason = classify(afterFindings);
    log(`  HARD STOP after filling: ${reason}`);
    for (const finding of afterFindings) log(`    - [${finding.kind}] ${finding.marker}: ${finding.detail}`);
    return abort(ctx, task, reason, afterFindings, page, results);
  }

  // ---- 9. assisted autofill hand-off -------------------------------------
  if (!mayClickSubmit(task)) {
    attempt.keepOpen = true;
    await screenshotToDisk(ctx, page, task, 'autofill');
    log('');
    log('  ============================================================');
    log('  ASSISTED AUTOFILL COMPLETE. THE FORM IS FILLED, NOT SUBMITTED.');
    log('  ------------------------------------------------------------');
    log('  The browser window is still open and is now yours.');
    log('   1. Read every field. Correct anything that is wrong.');
    log('   2. Click submit yourself if you are happy with it.');
    log('   3. Close the window, then press Ctrl+C here.');
    log('');
    log('  No result was sent to the server: this assistant never reports a');
    log('  submission it did not make. When you have clicked submit, record it');
    log('  with POST /api/v1/applications/<id>/mark-submitted (the "I submitted');
    log('  this" action in the app). Until then it stays in progress and is not');
    log('  offered to the assistant again.');
    log(`  Application id: ${task.application_id}`);
    log('  ============================================================');
    log('');
    return 'handoff';
  }

  // ---- 10. auto submit ----------------------------------------------------
  if (config.dryRun) {
    attempt.keepOpen = true;
    await screenshotToDisk(ctx, page, task, 'dryrun');
    log('');
    log('  ############################################################');
    log('  DRY RUN. THE SUBMIT BUTTON WAS NOT CLICKED.');
    log('  Everything up to the click was done: the form is filled and the');
    log('  guards all passed. Set DRY_RUN=false in .env to allow the click.');
    log('  No result was sent to the server. The window stays open for you.');
    log('  ############################################################');
    log('');
    return 'dry_run';
  }

  const submit = await findSubmitButton(page);
  if (submit === null) {
    log('  HARD STOP: no submit button could be identified.');
    return abort(
      ctx,
      task,
      'validation_failed',
      [{ kind: 'question', marker: 'submit', detail: 'No submit button found on the page', source: 'server' }],
      page,
      results,
    );
  }

  log(`  Clicking submit (mode=${task.mode}, may_click_submit=true, DRY_RUN=false).`);
  await submit.click();
  await page.waitForLoadState('networkidle', { timeout: 30_000 }).catch(() => undefined);

  const bodyText = await page.innerText('body').catch(() => '');
  const confirmationNumber = extractConfirmationNumber(bodyText);
  const screenshot = config.screenshotOnSuccess ? await screenshotBase64(page) : '';

  await api.reportResult(task.application_id, {
    outcome: 'submitted',
    confirmation_number: confirmationNumber,
    filled_fields: results as unknown[],
    screenshot_base64: screenshot,
    receipt: {
      url: page.url(),
      page_title: await page.title().catch(() => ''),
      submitted_at: new Date().toISOString(),
      mode: task.mode,
      connector_key: task.connector_key,
      job_title: task.job_title,
      company: task.company,
      confirmation_number: confirmationNumber,
      confirmation_excerpt: bodyText.slice(0, 400),
    },
  });
  log(`  Submitted. Confirmation: ${confirmationNumber || '(none found on the page)'}`);
  return 'submitted';
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/**
 * Merge the server's plan with the answers it just gave for the fields actually
 * on the page. Only server supplied values end up here.
 */
export function buildFieldSpecs(
  task: TaskOut,
  discovered: DiscoveredQuestion[],
  answers: ServerAnswer[],
): FieldSpec[] {
  const specs = new Map<string, FieldSpec>();

  for (const field of task.fields) {
    specs.set(field.question_external_id, {
      selector_hint: field.selector_hint,
      question_external_id: field.question_external_id,
      label: field.label,
      value: field.value,
      type: field.type,
      required: field.required,
    });
  }

  const byId = new Map(discovered.map((question) => [question.external_id, question]));
  for (const answer of answers) {
    const question = byId.get(answer.external_id);
    const existing = specs.get(answer.external_id);
    specs.set(answer.external_id, {
      selector_hint: existing?.selector_hint ?? answer.external_id,
      question_external_id: answer.external_id,
      label: question?.text ?? existing?.label ?? answer.external_id,
      value: answer.value ?? '',
      type: answer.type || question?.type || existing?.type || 'unknown',
      required: answer.required || question?.required || existing?.required || false,
    });
  }

  // File uploads discovered on the page are filled from attachments, not answers.
  for (const question of discovered) {
    if (question.type !== 'file') continue;
    if (specs.has(question.external_id)) continue;
    specs.set(question.external_id, {
      selector_hint: question.external_id,
      question_external_id: question.external_id,
      label: question.text,
      value: '',
      type: 'file',
      required: question.required,
    });
  }

  return [...specs.values()];
}

/** Pick the attachment whose role best fits a file field's label. */
export function pickAttachmentRole(label: string): 'resume' | 'cover_letter' | 'other' {
  const text = label.toLowerCase();
  if (text.includes('cover') || text.includes('letter')) return 'cover_letter';
  if (text.includes('resume') || text.includes('cv') || text.includes('curriculum')) return 'resume';
  return 'other';
}

async function downloadAttachments(
  ctx: RunnerContext,
  task: TaskOut,
  specs: FieldSpec[],
  discovered: DiscoveredQuestion[],
): Promise<Record<string, string[]>> {
  const fileFields = [
    ...specs.filter((spec) => spec.type === 'file'),
    ...discovered.filter((q) => q.type === 'file').map<FieldSpec>((q) => ({
      selector_hint: q.external_id,
      question_external_id: q.external_id,
      label: q.text,
      value: '',
      type: 'file',
      required: q.required,
    })),
  ];
  if (fileFields.length === 0 || task.attachments.length === 0) return {};

  const dir = await mkdtemp(path.join(os.tmpdir(), 'jobagent-assistant-'));
  const saved = new Map<string, string>();
  for (const attachment of task.attachments) {
    const document = await ctx.api.downloadDocument(attachment.document_id, attachment.filename);
    const target = path.join(dir, safeFilename(document.filename || attachment.filename));
    await writeFile(target, document.bytes);
    saved.set(attachment.role, target);
    ctx.log(`  Downloaded ${attachment.role}: ${document.filename} (${document.bytes.length} bytes)`);
  }

  const result: Record<string, string[]> = {};
  for (const field of fileFields) {
    const role = pickAttachmentRole(field.label);
    const direct = saved.get(role);
    const fallback = role === 'other' ? [...saved.values()][0] : undefined;
    const chosen = direct ?? fallback;
    if (chosen !== undefined) result[field.question_external_id] = [chosen];
  }
  return result;
}

export function safeFilename(filename: string): string {
  const base = path.basename(filename).replace(/[^A-Za-z0-9._-]/gu, '_');
  return base === '' ? 'attachment.bin' : base.slice(0, 120);
}

export function extractConfirmationNumber(text: string): string {
  for (const pattern of CONFIRMATION_PATTERNS) {
    const match = pattern.exec(text);
    const captured = match?.[1];
    if (captured !== undefined && captured.trim() !== '') return captured.trim();
  }
  return '';
}

async function findSubmitButton(page: Page): Promise<{ click(): Promise<void> } | null> {
  for (const selector of SUBMIT_SELECTORS) {
    const locator = page.locator(selector);
    let count = 0;
    try {
      count = await locator.count();
    } catch {
      continue;
    }
    if (count > 0) return locator.first();
  }
  return null;
}

async function screenshotBase64(page: Page): Promise<string> {
  try {
    const buffer = await page.screenshot({ fullPage: false });
    return buffer.toString('base64');
  } catch {
    return '';
  }
}

async function screenshotToDisk(ctx: RunnerContext, page: Page, task: TaskOut, label: string): Promise<void> {
  try {
    const dir = await mkdtemp(path.join(os.tmpdir(), 'jobagent-assistant-'));
    const file = path.join(dir, `${label}-${task.application_id}.png`);
    await page.screenshot({ path: file, fullPage: true });
    ctx.log(`  Screenshot: ${file}`);
  } catch {
    // A screenshot is a convenience, never a precondition.
  }
}

/**
 * Report a stop. Always outcome 'aborted' with a reason from
 * ASSISTANT_ABORT_REASONS, and never followed by another try.
 */
async function abort(
  ctx: RunnerContext,
  task: TaskOut,
  reason: string,
  findings: GuardFinding[],
  page: Page | null,
  filled: FillResult[] = [],
): Promise<RunOutcome> {
  const payload: ResultIn = {
    outcome: 'aborted',
    abort_reason: reason,
    error_message: findings[0]?.detail ?? `Assistant aborted: ${reason}`,
    guard_findings: findings as unknown[],
    filled_fields: filled as unknown[],
  };
  if (page !== null) {
    const shot = await screenshotBase64(page);
    if (shot !== '') payload.screenshot_base64 = shot;
  }
  await ctx.api.reportResult(task.application_id, payload);
  ctx.log(`  Reported abort "${reason}". A review task is waiting for you in the app. Not retrying.`);
  return 'aborted';
}

async function closeQuietly(attempt: Attempt): Promise<void> {
  try {
    if (attempt.browser !== null) await attempt.browser.close();
  } catch {
    // Nothing useful to do if the browser is already gone.
  } finally {
    attempt.browser = null;
    attempt.context = null;
    attempt.page = null;
  }
}

export class RuntimeExceeded extends Error {
  constructor(seconds: number) {
    super(`Attempt exceeded MAX_RUNTIME_SECONDS (${seconds}s) and was stopped.`);
    this.name = 'RuntimeExceeded';
  }
}

export function withTimeout<T>(promise: Promise<T>, ms: number, seconds: number): Promise<T> {
  let timer: NodeJS.Timeout | undefined;
  const timeout = new Promise<never>((_resolve, reject) => {
    timer = setTimeout(() => reject(new RuntimeExceeded(seconds)), ms);
  });
  return Promise.race([promise, timeout]).finally(() => {
    if (timer !== undefined) clearTimeout(timer);
  }) as Promise<T>;
}
