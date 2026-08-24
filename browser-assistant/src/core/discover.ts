/**
 * Read the real form and describe it to the server.
 *
 * The assistant reports what it sees; it never decides what the answer is. The
 * shapes here mirror DiscoveredQuestion in backend/app/api/v1/assistant.py and
 * the QuestionType vocabulary in backend/app/core/enums.py.
 *
 * EEO and demographic questions are labelled as such so the server can apply its
 * own rule for them. They are never inferred from anything: docs/COMPLIANCE.md
 * section 3 item 6.
 */

export const QUESTION_TYPES = [
  'short_text',
  'long_text',
  'boolean',
  'single_select',
  'multi_select',
  'number',
  'date',
  'file',
  'eeo',
  'unknown',
] as const;

export type QuestionType = (typeof QUESTION_TYPES)[number];

export interface DiscoveredQuestion {
  external_id: string;
  text: string;
  type: QuestionType;
  required: boolean;
  options: string[];
}

/** What a browser pass reports about one control. Plain data, easy to fabricate in a test. */
export interface RawField {
  tag: string;
  type: string;
  name: string;
  id: string;
  dataQa: string;
  label: string;
  required: boolean;
  multiple: boolean;
  options: string[];
}

export const EEO_KEYWORDS: string[] = [
  'gender',
  'race',
  'ethnicity',
  'ethnic',
  'veteran',
  'disability',
  'disabled',
  'hispanic',
  'latino',
  'latinx',
  'sexual orientation',
  'self-identify',
  'self identify',
  'self-identification',
  'protected veteran',
  'eeo',
  'equal employment opportunity',
  'demographic',
  'transgender',
  'pronouns',
];

const YES_NO_WORDS = new Set(['yes', 'no', 'true', 'false', 'y', 'n']);

const SHORT_TEXT_INPUT_TYPES = new Set(['text', 'email', 'tel', 'url', 'search', '']);
const NUMBER_INPUT_TYPES = new Set(['number', 'range']);
const DATE_INPUT_TYPES = new Set(['date', 'datetime-local', 'month', 'week', 'time']);

/** A question about a protected characteristic. Always flagged, never guessed. */
export function isEeoQuestion(label: string): boolean {
  const text = label.toLowerCase();
  return EEO_KEYWORDS.some((keyword) => text.includes(keyword));
}

/** An asterisk in the label, or the required attribute, both mean required. */
export function isRequiredFromLabel(label: string): boolean {
  return /\*/u.test(label) || /\(required\)/iu.test(label) || /\brequired\b/iu.test(label);
}

/** Strip the required marker and collapse whitespace so the server sees clean text. */
export function cleanLabel(label: string): string {
  return label
    .replace(/\s*\*\s*$/u, '')
    .replace(/\s*\(required\)\s*$/iu, '')
    .replace(/\s+/gu, ' ')
    .trim();
}

/**
 * Map one control onto a backend QuestionType. Pure, so every branch is testable.
 */
export function mapInputType(field: RawField): QuestionType {
  if (isEeoQuestion(field.label)) return 'eeo';

  const tag = field.tag.toLowerCase();
  const type = field.type.toLowerCase();

  if (tag === 'textarea') return 'long_text';
  if (tag === 'select') return field.multiple ? 'multi_select' : 'single_select';

  if (tag === 'input') {
    if (type === 'file') return 'file';
    if (type === 'checkbox') return field.options.length > 1 ? 'multi_select' : 'boolean';
    if (type === 'radio') {
      const looksBoolean =
        field.options.length > 0 && field.options.every((option) => YES_NO_WORDS.has(option.trim().toLowerCase()));
      return looksBoolean ? 'boolean' : 'single_select';
    }
    if (NUMBER_INPUT_TYPES.has(type)) return 'number';
    if (DATE_INPUT_TYPES.has(type)) return 'date';
    if (SHORT_TEXT_INPUT_TYPES.has(type)) return 'short_text';
    return 'unknown';
  }

  return 'unknown';
}

/** Build the payload entry for one control. */
export function buildQuestion(field: RawField): DiscoveredQuestion {
  const label = cleanLabel(field.label) || field.name || field.id || field.dataQa;
  const externalId = field.name || field.id || field.dataQa || slug(label);
  return {
    external_id: externalId,
    text: label,
    type: mapInputType({ ...field, label: field.label || label }),
    required: field.required || isRequiredFromLabel(field.label),
    options: field.options,
  };
}

export function slug(text: string): string {
  return text
    .toLowerCase()
    .replace(/[^a-z0-9]+/gu, '_')
    .replace(/^_+|_+$/gu, '')
    .slice(0, 120);
}

/** Drop controls that carry no question: hidden inputs, buttons, honeypots. */
const IGNORED_INPUT_TYPES = new Set(['hidden', 'submit', 'button', 'reset', 'image', 'password']);

export function isQuestionField(field: RawField): boolean {
  const tag = field.tag.toLowerCase();
  if (tag === 'textarea' || tag === 'select') return true;
  if (tag !== 'input') return false;
  return !IGNORED_INPUT_TYPES.has(field.type.toLowerCase());
}

export function buildQuestions(fields: RawField[]): DiscoveredQuestion[] {
  const seen = new Set<string>();
  const questions: DiscoveredQuestion[] = [];
  for (const field of fields) {
    if (!isQuestionField(field)) continue;
    const question = buildQuestion(field);
    if (question.external_id === '' || seen.has(question.external_id)) continue;
    seen.add(question.external_id);
    questions.push(question);
  }
  return questions;
}

// ---------------------------------------------------------------------------
// Browser pass
// ---------------------------------------------------------------------------

import type { Page } from 'playwright';

/** Read every visible control on the page and describe it as RawField data. */
export async function readRawFields(page: Page): Promise<RawField[]> {
  return page.evaluate(() => {
    function labelFor(element: Element): string {
      const input = element as HTMLInputElement;
      const id = input.getAttribute('id');
      if (id) {
        const explicit = document.querySelector(`label[for="${CSS.escape(id)}"]`);
        if (explicit && explicit.textContent) return explicit.textContent.trim();
      }
      const wrapping = element.closest('label');
      if (wrapping && wrapping.textContent) return wrapping.textContent.trim();
      const aria = input.getAttribute('aria-label');
      if (aria) return aria.trim();
      const labelledBy = input.getAttribute('aria-labelledby');
      if (labelledBy) {
        const target = document.getElementById(labelledBy);
        if (target && target.textContent) return target.textContent.trim();
      }
      const placeholder = input.getAttribute('placeholder');
      if (placeholder) return placeholder.trim();
      const group = element.closest('fieldset');
      const legend = group ? group.querySelector('legend') : null;
      if (legend && legend.textContent) return legend.textContent.trim();
      return input.getAttribute('name') ?? '';
    }

    const results: {
      tag: string;
      type: string;
      name: string;
      id: string;
      dataQa: string;
      label: string;
      required: boolean;
      multiple: boolean;
      options: string[];
    }[] = [];
    const radioGroups = new Set<string>();

    const nodes = Array.from(document.querySelectorAll('input, textarea, select'));
    for (const node of nodes) {
      const element = node as HTMLInputElement & HTMLSelectElement;
      const tag = element.tagName.toLowerCase();
      const type = (element.type ?? '').toLowerCase();
      const name = element.getAttribute('name') ?? '';
      const id = element.getAttribute('id') ?? '';
      const dataQa = element.getAttribute('data-qa') ?? '';

      if (type === 'radio' && name !== '') {
        if (radioGroups.has(name)) continue;
        radioGroups.add(name);
      }

      let options: string[] = [];
      let multiple = false;
      if (tag === 'select') {
        multiple = Boolean(element.multiple);
        options = Array.from(element.options).map((option) => (option.textContent ?? option.value).trim());
      } else if (type === 'radio' && name !== '') {
        const group = Array.from(
          document.querySelectorAll(`input[type="radio"][name="${CSS.escape(name)}"]`),
        ) as HTMLInputElement[];
        options = group.map((radio) => {
          const radioId = radio.getAttribute('id');
          const explicit = radioId ? document.querySelector(`label[for="${CSS.escape(radioId)}"]`) : null;
          const text = explicit?.textContent?.trim() ?? radio.getAttribute('aria-label') ?? radio.value;
          return (text ?? '').trim();
        });
      }

      const style = window.getComputedStyle(element);
      const hidden = style.display === 'none' || style.visibility === 'hidden';
      if (hidden && type !== 'file') continue;

      results.push({
        tag,
        type,
        name,
        id,
        dataQa,
        label: labelFor(element),
        required: Boolean(element.required) || element.getAttribute('aria-required') === 'true',
        multiple,
        options,
      });
    }
    return results;
  });
}

/** Full discovery pass: read the page, then map it with the pure helpers. */
export async function discoverQuestions(page: Page): Promise<DiscoveredQuestion[]> {
  const fields = await readRawFields(page);
  return buildQuestions(fields);
}
