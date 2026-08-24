/**
 * Resilient form filling.
 *
 * Two rules govern every line below:
 *
 *   1. The assistant only ever writes a value the SERVER supplied. There is no
 *      code path that derives, guesses, completes or "best effort" fills a
 *      value. If the server did not answer a question, the field stays empty and
 *      the attempt goes to review.
 *   2. A REQUIRED field that cannot be located is a validation_failed abort. It
 *      is never approximated by filling the nearest looking input.
 *
 * The locator logic lives behind a small adapter interface so the ordering and
 * the decision making can be unit tested without launching a browser.
 */

export interface FieldSpec {
  selector_hint: string;
  question_external_id: string;
  label: string;
  value: string;
  type: string;
  required: boolean;
}

export interface OptionCandidate {
  value: string;
  label: string;
}

export type OptionInput = string | OptionCandidate;

export type StrategyKind = 'css' | 'name' | 'id' | 'data-qa' | 'label' | 'placeholder' | 'aria-label';

export interface Strategy {
  kind: StrategyKind;
  /** CSS selector, or the text used with getByLabel for kind 'label'. */
  selector: string;
}

export interface FillResult {
  /** The field's external id, which is what the server keys answers by. */
  name: string;
  matched: boolean;
  selector: string;
  ok: boolean;
  reason: string;
}

export type ElementKind = 'text' | 'textarea' | 'select' | 'checkbox' | 'radio' | 'file' | 'other';

/** An element the adapter found and can act on. */
export interface LocatedElement {
  kind: ElementKind;
  /** Present for select elements, and for radio groups. */
  options: OptionCandidate[];
  setText(value: string): Promise<void>;
  setChecked(checked: boolean): Promise<void>;
  chooseOption(option: OptionCandidate): Promise<void>;
  setFiles(paths: string[]): Promise<void>;
}

export interface FormAdapter {
  /** Return the element for this strategy, or null when nothing matches. */
  resolve(strategy: Strategy): Promise<LocatedElement | null>;
}

// ---------------------------------------------------------------------------
// Pure helpers
// ---------------------------------------------------------------------------

const YES_VALUES = new Set(['yes', 'true', '1', 'y', 'on', 'checked', 'agree', 'i agree', 'accept']);
const NO_VALUES = new Set(['no', 'false', '0', 'n', 'off', 'unchecked', 'decline']);

/** Map a server value onto a boolean, or null when it is not a yes/no value. */
export function toBoolean(value: string): boolean | null {
  const normalized = value.trim().toLowerCase();
  if (normalized === '') return null;
  if (YES_VALUES.has(normalized)) return true;
  if (NO_VALUES.has(normalized)) return false;
  return null;
}

function normalizeOption(option: OptionInput): OptionCandidate {
  return typeof option === 'string' ? { value: option, label: option } : option;
}

function squash(text: string): string {
  return text.trim().toLowerCase().replace(/\s+/gu, ' ');
}

/**
 * Choose the option that corresponds to a server supplied value.
 *
 * Order: exact label, exact value, case insensitive label, case insensitive
 * value, then a yes/no synonym match. Returns null rather than a near miss:
 * picking "Yes, with sponsorship" for a plain "yes" would be inventing an
 * answer, which is exactly what this program must not do.
 */
export function matchOption(options: OptionInput[], value: string): OptionCandidate | null {
  const candidates = options.map(normalizeOption);
  if (candidates.length === 0) return null;
  const raw = value.trim();
  if (raw === '') return null;

  for (const option of candidates) if (option.label === raw) return option;
  for (const option of candidates) if (option.value === raw) return option;

  const wanted = squash(raw);
  for (const option of candidates) if (squash(option.label) === wanted) return option;
  for (const option of candidates) if (squash(option.value) === wanted) return option;

  const asBoolean = toBoolean(raw);
  if (asBoolean !== null) {
    for (const option of candidates) {
      const optionBoolean = toBoolean(option.label) ?? toBoolean(option.value);
      if (optionBoolean !== null && optionBoolean === asBoolean) return option;
    }
  }

  return null;
}

/** Escape a value for use inside a CSS attribute selector. */
export function cssAttributeValue(value: string): string {
  return value.replace(/\\/gu, '\\\\').replace(/"/gu, '\\"');
}

/**
 * The locator ladder, in order. Pure so the ordering itself is testable.
 */
export function buildStrategies(spec: Pick<FieldSpec, 'selector_hint' | 'question_external_id' | 'label'>): Strategy[] {
  const strategies: Strategy[] = [];
  const hints: string[] = [];
  for (const candidate of [spec.selector_hint, spec.question_external_id]) {
    const trimmed = (candidate ?? '').trim();
    if (trimmed !== '' && !hints.includes(trimmed)) hints.push(trimmed);
  }

  for (const hint of hints) {
    if (/^[#.[]/u.test(hint)) {
      strategies.push({ kind: 'css', selector: hint });
      continue;
    }
    const escaped = cssAttributeValue(hint);
    strategies.push({ kind: 'name', selector: `[name="${escaped}"]` });
    strategies.push({ kind: 'id', selector: `[id="${escaped}"]` });
    strategies.push({ kind: 'data-qa', selector: `[data-qa="${escaped}"]` });
  }

  const label = (spec.label ?? '').trim();
  if (label !== '') {
    const escaped = cssAttributeValue(label);
    strategies.push({ kind: 'label', selector: label });
    strategies.push({ kind: 'placeholder', selector: `[placeholder="${escaped}"]` });
    strategies.push({ kind: 'aria-label', selector: `[aria-label="${escaped}"]` });
  }

  return strategies;
}

// ---------------------------------------------------------------------------
// Filling
// ---------------------------------------------------------------------------

export interface FillOptions {
  /** Absolute paths to attach, keyed by the field's external id. */
  filesByField?: Record<string, string[]>;
}

function failure(spec: FieldSpec, selector: string, matched: boolean, reason: string): FillResult {
  return { name: spec.question_external_id, matched, selector, ok: false, reason };
}

/**
 * Fill one field. Never throws: a problem is returned as ok:false with a reason
 * so the runner can decide, once, whether the whole attempt has to stop.
 */
export async function fillField(
  adapter: FormAdapter,
  spec: FieldSpec,
  options: FillOptions = {},
): Promise<FillResult> {
  const strategies = buildStrategies(spec);
  if (strategies.length === 0) {
    return failure(spec, '', false, 'no_locator_hint');
  }

  let element: LocatedElement | null = null;
  let usedSelector = '';
  for (const strategy of strategies) {
    let candidate: LocatedElement | null = null;
    try {
      candidate = await adapter.resolve(strategy);
    } catch {
      candidate = null; // A bad selector is not a reason to crash the attempt.
    }
    if (candidate !== null) {
      element = candidate;
      usedSelector = `${strategy.kind}=${strategy.selector}`;
      break;
    }
  }

  if (element === null) {
    return failure(spec, strategies.map((s) => s.selector).join(' | '), false, 'no_locator_matched');
  }

  const files = options.filesByField?.[spec.question_external_id] ?? [];
  if (element.kind === 'file') {
    if (files.length === 0) {
      return failure(spec, usedSelector, true, 'no_attachment_available');
    }
    try {
      await element.setFiles(files);
    } catch (error) {
      return failure(spec, usedSelector, true, `set_files_failed: ${message(error)}`);
    }
    return { name: spec.question_external_id, matched: true, selector: usedSelector, ok: true, reason: 'file_attached' };
  }

  const value = (spec.value ?? '').trim();
  if (value === '') {
    // Nothing to write. The assistant does not fabricate a placeholder.
    return failure(spec, usedSelector, true, 'no_value_supplied');
  }

  try {
    switch (element.kind) {
      case 'checkbox': {
        const checked = toBoolean(value);
        if (checked === null) {
          return failure(spec, usedSelector, true, `value_not_boolean: "${value}"`);
        }
        await element.setChecked(checked);
        break;
      }
      case 'radio': {
        const option = matchOption(element.options, value);
        if (option === null) {
          return failure(spec, usedSelector, true, `no_matching_radio_option: "${value}"`);
        }
        await element.chooseOption(option);
        break;
      }
      case 'select': {
        const option = matchOption(element.options, value);
        if (option === null) {
          return failure(spec, usedSelector, true, `no_matching_select_option: "${value}"`);
        }
        await element.chooseOption(option);
        break;
      }
      case 'text':
      case 'textarea':
      case 'other': {
        await element.setText(value);
        break;
      }
      // 'file' cannot reach here: it is handled above, before the value check.
    }
  } catch (error) {
    return failure(spec, usedSelector, true, `fill_failed: ${message(error)}`);
  }

  return { name: spec.question_external_id, matched: true, selector: usedSelector, ok: true, reason: 'filled' };
}

export async function fillAll(
  adapter: FormAdapter,
  specs: FieldSpec[],
  options: FillOptions = {},
): Promise<FillResult[]> {
  const results: FillResult[] = [];
  for (const spec of specs) {
    results.push(await fillField(adapter, spec, options));
  }
  return results;
}

/**
 * The required fields that could not be filled. A non-empty list is a
 * validation_failed abort - never a guess, never a partial submission.
 */
export function requiredFailures(specs: FieldSpec[], results: FillResult[]): FillResult[] {
  const required = new Set(specs.filter((spec) => spec.required).map((spec) => spec.question_external_id));
  return results.filter((result) => required.has(result.name) && !result.ok);
}

function message(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

// ---------------------------------------------------------------------------
// Playwright adapter
// ---------------------------------------------------------------------------

import type { Page, Locator } from 'playwright';

async function describeElement(locator: Locator): Promise<{ kind: ElementKind; options: OptionCandidate[] }> {
  const info = await locator.evaluate((node: Element) => {
    const tag = node.tagName.toLowerCase();
    const type = (node as HTMLInputElement).type ? String((node as HTMLInputElement).type).toLowerCase() : '';
    const options: { value: string; label: string }[] = [];
    if (tag === 'select') {
      for (const option of Array.from((node as HTMLSelectElement).options)) {
        options.push({ value: option.value, label: (option.textContent ?? '').trim() });
      }
    }
    return { tag, type, options };
  });

  const options = info.options.map((option) => ({ value: option.value, label: option.label }));
  if (info.tag === 'select') return { kind: 'select', options };
  if (info.tag === 'textarea') return { kind: 'textarea', options };
  if (info.tag === 'input') {
    if (info.type === 'checkbox') return { kind: 'checkbox', options };
    if (info.type === 'radio') return { kind: 'radio', options };
    if (info.type === 'file') return { kind: 'file', options };
    return { kind: 'text', options };
  }
  return { kind: 'other', options };
}

/** Radio groups are addressed by name; collect the group's values as options. */
async function radioOptions(page: Page, locator: Locator): Promise<OptionCandidate[]> {
  const name = await locator.getAttribute('name');
  if (name === null || name === '') return [];
  const group = page.locator(`input[type="radio"][name="${cssAttributeValue(name)}"]`);
  const count = await group.count();
  const options: OptionCandidate[] = [];
  for (let index = 0; index < count; index += 1) {
    const radio = group.nth(index);
    const value = (await radio.getAttribute('value')) ?? '';
    let label = (await radio.getAttribute('aria-label')) ?? '';
    if (label === '') {
      const id = await radio.getAttribute('id');
      if (id !== null && id !== '') {
        const labelFor = page.locator(`label[for="${cssAttributeValue(id)}"]`);
        if ((await labelFor.count()) > 0) label = (await labelFor.first().innerText()).trim();
      }
    }
    options.push({ value, label: label === '' ? value : label });
  }
  return options;
}

function wrapLocator(page: Page, locator: Locator, kind: ElementKind, options: OptionCandidate[]): LocatedElement {
  return {
    kind,
    options,
    async setText(value: string): Promise<void> {
      await locator.fill(value);
    },
    async setChecked(checked: boolean): Promise<void> {
      await locator.setChecked(checked);
    },
    async chooseOption(option: OptionCandidate): Promise<void> {
      if (kind === 'select') {
        if (option.value !== '') {
          await locator.selectOption({ value: option.value });
        } else {
          await locator.selectOption({ label: option.label });
        }
        return;
      }
      const name = await locator.getAttribute('name');
      if (name !== null && name !== '' && option.value !== '') {
        await page
          .locator(`input[type="radio"][name="${cssAttributeValue(name)}"][value="${cssAttributeValue(option.value)}"]`)
          .first()
          .check();
        return;
      }
      await locator.check();
    },
    async setFiles(paths: string[]): Promise<void> {
      await locator.setInputFiles(paths);
    },
  };
}

/** Bind the pure filling logic to a real Playwright page. */
export function createPlaywrightAdapter(page: Page): FormAdapter {
  return {
    async resolve(strategy: Strategy): Promise<LocatedElement | null> {
      const locator: Locator =
        strategy.kind === 'label'
          ? page.getByLabel(strategy.selector, { exact: false })
          : page.locator(strategy.selector);

      const count = await locator.count();
      if (count === 0) return null;
      const first = locator.first();
      const { kind, options } = await describeElement(first);
      const resolvedOptions = kind === 'radio' ? await radioOptions(page, first) : options;
      return wrapLocator(page, first, kind, resolvedOptions);
    },
  };
}
