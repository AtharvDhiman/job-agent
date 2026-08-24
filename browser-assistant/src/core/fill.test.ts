import { describe, expect, it } from 'vitest';

import {
  buildStrategies,
  cssAttributeValue,
  fillAll,
  fillField,
  matchOption,
  requiredFailures,
  toBoolean,
  type ElementKind,
  type FieldSpec,
  type FormAdapter,
  type LocatedElement,
  type OptionCandidate,
  type Strategy,
} from './fill.js';
import { QUESTION_TYPES, buildQuestion, isEeoQuestion, mapInputType, type QuestionType, type RawField } from './discover.js';

// ---------------------------------------------------------------------------
// Test doubles: no browser involved anywhere in this file.
// ---------------------------------------------------------------------------

interface Recorder {
  text: string[];
  checked: boolean[];
  chosen: OptionCandidate[];
  files: string[][];
}

function stubElement(kind: ElementKind, options: OptionCandidate[], recorder: Recorder): LocatedElement {
  return {
    kind,
    options,
    async setText(value: string): Promise<void> {
      recorder.text.push(value);
    },
    async setChecked(value: boolean): Promise<void> {
      recorder.checked.push(value);
    },
    async chooseOption(option: OptionCandidate): Promise<void> {
      recorder.chosen.push(option);
    },
    async setFiles(paths: string[]): Promise<void> {
      recorder.files.push(paths);
    },
  };
}

function newRecorder(): Recorder {
  return { text: [], checked: [], chosen: [], files: [] };
}

/** Resolves only for the strategies listed in `answers`, in whatever order fillField asks. */
function stubAdapter(
  answers: Partial<Record<Strategy['kind'], LocatedElement>>,
  seen: Strategy[] = [],
): FormAdapter {
  return {
    async resolve(strategy: Strategy): Promise<LocatedElement | null> {
      seen.push(strategy);
      return answers[strategy.kind] ?? null;
    },
  };
}

function spec(overrides: Partial<FieldSpec> = {}): FieldSpec {
  return {
    selector_hint: 'work_auth',
    question_external_id: 'work_auth',
    label: 'Are you authorized to work in the US?',
    value: 'yes',
    type: 'boolean',
    required: true,
    ...overrides,
  };
}

// ---------------------------------------------------------------------------

describe('toBoolean', () => {
  it('maps the yes family to true', () => {
    for (const value of ['yes', 'Yes', 'YES', 'true', 'True', '1', 'y', 'on']) {
      expect(toBoolean(value), value).toBe(true);
    }
  });

  it('maps the no family to false', () => {
    for (const value of ['no', 'No', 'false', '0', 'n', 'off']) {
      expect(toBoolean(value), value).toBe(false);
    }
  });

  it('returns null for anything that is not a yes/no value', () => {
    expect(toBoolean('')).toBeNull();
    expect(toBoolean('maybe')).toBeNull();
    expect(toBoolean('prefer not to say')).toBeNull();
  });
});

describe('matchOption', () => {
  it('prefers an exact label match', () => {
    const options = [
      { value: 'a', label: 'Yes' },
      { value: 'b', label: 'Yes, with sponsorship' },
    ];
    expect(matchOption(options, 'Yes')).toEqual({ value: 'a', label: 'Yes' });
  });

  it('matches option text case insensitively', () => {
    const options = ['Yes', 'No', 'Prefer not to say'];
    expect(matchOption(options, 'yes')).toEqual({ value: 'Yes', label: 'Yes' });
    expect(matchOption(options, 'PREFER NOT TO SAY')).toEqual({
      value: 'Prefer not to say',
      label: 'Prefer not to say',
    });
  });

  it('matches on the underlying value when the label differs', () => {
    const options = [{ value: 'us_citizen', label: 'United States citizen' }];
    expect(matchOption(options, 'us_citizen')?.value).toBe('us_citizen');
    expect(matchOption(options, 'United States Citizen')?.value).toBe('us_citizen');
  });

  it('maps yes/no synonyms onto boolean options', () => {
    const options = [
      { value: '1', label: 'Yes' },
      { value: '0', label: 'No' },
    ];
    expect(matchOption(options, 'true')?.value).toBe('1');
    expect(matchOption(options, 'False')?.value).toBe('0');
  });

  it('returns null rather than a near miss', () => {
    const options = ['Yes, with sponsorship', 'No, I do not need sponsorship'];
    expect(matchOption(options, 'maybe')).toBeNull();
    expect(matchOption(options, '')).toBeNull();
    expect(matchOption([], 'yes')).toBeNull();
  });
});

describe('buildStrategies', () => {
  it('tries name, id, data-qa, label, placeholder then aria-label in order', () => {
    const strategies = buildStrategies({
      selector_hint: 'first_name',
      question_external_id: 'first_name',
      label: 'First name',
    });
    expect(strategies.map((strategy) => strategy.kind)).toEqual([
      'name',
      'id',
      'data-qa',
      'label',
      'placeholder',
      'aria-label',
    ]);
    expect(strategies[0]?.selector).toBe('[name="first_name"]');
    expect(strategies[1]?.selector).toBe('[id="first_name"]');
    expect(strategies[2]?.selector).toBe('[data-qa="first_name"]');
  });

  it('uses a raw CSS hint as given', () => {
    const strategies = buildStrategies({
      selector_hint: '#question_12345',
      question_external_id: 'q12345',
      label: 'Why here?',
    });
    expect(strategies[0]).toEqual({ kind: 'css', selector: '#question_12345' });
  });

  it('escapes quotes in attribute selectors', () => {
    expect(cssAttributeValue('a"b')).toBe('a\\"b');
  });
});

describe('fillField', () => {
  it('returns ok:false with a reason when nothing matches, instead of throwing', async () => {
    const seen: Strategy[] = [];
    const result = await fillField(stubAdapter({}, seen), spec());
    expect(result.ok).toBe(false);
    expect(result.matched).toBe(false);
    expect(result.reason).toBe('no_locator_matched');
    expect(seen.length).toBeGreaterThan(3);
  });

  it('does not throw when the adapter itself blows up', async () => {
    const adapter: FormAdapter = {
      async resolve(): Promise<LocatedElement | null> {
        throw new Error('invalid selector');
      },
    };
    const result = await fillField(adapter, spec());
    expect(result.ok).toBe(false);
    expect(result.reason).toBe('no_locator_matched');
  });

  it('writes text values verbatim', async () => {
    const recorder = newRecorder();
    const adapter = stubAdapter({ name: stubElement('text', [], recorder) });
    const result = await fillField(
      adapter,
      spec({ type: 'short_text', value: 'Ada Lovelace', label: 'Full name' }),
    );
    expect(result.ok).toBe(true);
    expect(recorder.text).toEqual(['Ada Lovelace']);
  });

  it('never writes a value the server did not supply', async () => {
    const recorder = newRecorder();
    const adapter = stubAdapter({ name: stubElement('text', [], recorder) });
    const result = await fillField(adapter, spec({ value: '   ' }));
    expect(result.ok).toBe(false);
    expect(result.reason).toBe('no_value_supplied');
    expect(recorder.text).toEqual([]);
  });

  it('maps yes/no onto a checkbox', async () => {
    const recorder = newRecorder();
    const adapter = stubAdapter({ name: stubElement('checkbox', [], recorder) });
    expect((await fillField(adapter, spec({ value: 'Yes' }))).ok).toBe(true);
    expect((await fillField(adapter, spec({ value: 'no' }))).ok).toBe(true);
    expect(recorder.checked).toEqual([true, false]);
  });

  it('refuses a checkbox value that is not a yes/no', async () => {
    const recorder = newRecorder();
    const adapter = stubAdapter({ name: stubElement('checkbox', [], recorder) });
    const result = await fillField(adapter, spec({ value: 'sometimes' }));
    expect(result.ok).toBe(false);
    expect(result.reason).toContain('value_not_boolean');
    expect(recorder.checked).toEqual([]);
  });

  it('selects a select option case insensitively', async () => {
    const recorder = newRecorder();
    const options = [
      { value: 'ny', label: 'New York' },
      { value: 'sf', label: 'San Francisco' },
    ];
    const adapter = stubAdapter({ name: stubElement('select', options, recorder) });
    const result = await fillField(adapter, spec({ type: 'single_select', value: 'san francisco' }));
    expect(result.ok).toBe(true);
    expect(recorder.chosen).toEqual([{ value: 'sf', label: 'San Francisco' }]);
  });

  it('reports an unmatched select option instead of picking one', async () => {
    const recorder = newRecorder();
    const adapter = stubAdapter({ name: stubElement('select', [{ value: 'ny', label: 'New York' }], recorder) });
    const result = await fillField(adapter, spec({ type: 'single_select', value: 'Berlin' }));
    expect(result.ok).toBe(false);
    expect(result.reason).toContain('no_matching_select_option');
    expect(recorder.chosen).toEqual([]);
  });

  it('chooses the right radio in a yes/no group', async () => {
    const recorder = newRecorder();
    const options = [
      { value: '1', label: 'Yes' },
      { value: '0', label: 'No' },
    ];
    const adapter = stubAdapter({ name: stubElement('radio', options, recorder) });
    const result = await fillField(adapter, spec({ type: 'boolean', value: 'true' }));
    expect(result.ok).toBe(true);
    expect(recorder.chosen[0]?.value).toBe('1');
  });

  it('attaches a file when one was downloaded for the field', async () => {
    const recorder = newRecorder();
    const adapter = stubAdapter({ name: stubElement('file', [], recorder) });
    const result = await fillField(adapter, spec({ type: 'file', value: '', question_external_id: 'resume' }), {
      filesByField: { resume: ['/tmp/resume.pdf'] },
    });
    expect(result.ok).toBe(true);
    expect(recorder.files).toEqual([['/tmp/resume.pdf']]);
  });

  it('reports a file field with no document available', async () => {
    const recorder = newRecorder();
    const adapter = stubAdapter({ name: stubElement('file', [], recorder) });
    const result = await fillField(adapter, spec({ type: 'file', value: '', question_external_id: 'resume' }));
    expect(result.ok).toBe(false);
    expect(result.reason).toBe('no_attachment_available');
  });

  it('surfaces an error raised while filling as a reason', async () => {
    const element: LocatedElement = {
      kind: 'text',
      options: [],
      async setText(): Promise<void> {
        throw new Error('element is not visible');
      },
      async setChecked(): Promise<void> {},
      async chooseOption(): Promise<void> {},
      async setFiles(): Promise<void> {},
    };
    const result = await fillField(stubAdapter({ name: element }), spec({ value: 'Ada' }));
    expect(result.ok).toBe(false);
    expect(result.reason).toContain('fill_failed');
  });
});

describe('fillAll and requiredFailures', () => {
  it('flags required fields that could not be filled', async () => {
    const recorder = newRecorder();
    const specs = [
      spec({ question_external_id: 'first_name', selector_hint: 'first_name', value: 'Ada', type: 'short_text' }),
      spec({ question_external_id: 'ghost', selector_hint: 'ghost', label: 'Not on the page', required: true }),
    ];
    const adapter: FormAdapter = {
      async resolve(strategy: Strategy): Promise<LocatedElement | null> {
        return strategy.selector.includes('first_name') ? stubElement('text', [], recorder) : null;
      },
    };
    const results = await fillAll(adapter, specs);
    expect(results).toHaveLength(2);
    const failures = requiredFailures(specs, results);
    expect(failures.map((failure) => failure.name)).toEqual(['ghost']);
  });

  it('ignores optional fields that were not found', async () => {
    const specs = [spec({ question_external_id: 'nice_to_have', required: false })];
    const results = await fillAll(stubAdapter({}), specs);
    expect(requiredFailures(specs, results)).toEqual([]);
  });
});

// ---------------------------------------------------------------------------
// Question type mapping, kept browser free for the same reason.
// ---------------------------------------------------------------------------

function raw(overrides: Partial<RawField> = {}): RawField {
  return {
    tag: 'input',
    type: 'text',
    name: 'field',
    id: '',
    dataQa: '',
    label: 'A question',
    required: false,
    multiple: false,
    options: [],
    ...overrides,
  };
}

describe('mapInputType', () => {
  it('produces every QuestionType the backend knows about', () => {
    const cases: { field: RawField; expected: QuestionType }[] = [
      { field: raw({ tag: 'input', type: 'text' }), expected: 'short_text' },
      { field: raw({ tag: 'input', type: 'email' }), expected: 'short_text' },
      { field: raw({ tag: 'textarea', type: 'textarea' }), expected: 'long_text' },
      { field: raw({ tag: 'input', type: 'checkbox' }), expected: 'boolean' },
      { field: raw({ tag: 'input', type: 'radio', options: ['Yes', 'No'] }), expected: 'boolean' },
      { field: raw({ tag: 'select', type: 'select-one' }), expected: 'single_select' },
      { field: raw({ tag: 'select', type: 'select-multiple', multiple: true }), expected: 'multi_select' },
      { field: raw({ tag: 'input', type: 'number' }), expected: 'number' },
      { field: raw({ tag: 'input', type: 'date' }), expected: 'date' },
      { field: raw({ tag: 'input', type: 'file' }), expected: 'file' },
      { field: raw({ tag: 'input', type: 'text', label: 'Gender' }), expected: 'eeo' },
      { field: raw({ tag: 'input', type: 'color' }), expected: 'unknown' },
    ];
    for (const testCase of cases) {
      expect(mapInputType(testCase.field), `${testCase.field.tag}/${testCase.field.type}`).toBe(testCase.expected);
    }
    const produced = new Set(cases.map((testCase) => testCase.expected));
    for (const type of QUESTION_TYPES) {
      expect(produced.has(type), `no case produces ${type}`).toBe(true);
    }
  });

  it('treats a radio group with real choices as a single select', () => {
    expect(mapInputType(raw({ type: 'radio', options: ['Remote', 'Hybrid', 'Onsite'] }))).toBe('single_select');
  });
});

describe('isEeoQuestion', () => {
  it('flags demographic questions whatever the wording', () => {
    const labels = [
      'What is your gender?',
      'Race / Ethnicity',
      'Are you a protected veteran?',
      'Disability status',
      'Are you Hispanic or Latino?',
      'Sexual orientation',
      'Voluntary self-identification',
    ];
    for (const label of labels) expect(isEeoQuestion(label), label).toBe(true);
  });

  it('leaves ordinary questions alone', () => {
    expect(isEeoQuestion('What is your notice period?')).toBe(false);
    expect(isEeoQuestion('Full name')).toBe(false);
  });
});

describe('buildQuestion', () => {
  it('reads required from an asterisk in the label and cleans it up', () => {
    const question = buildQuestion(raw({ name: 'first_name', label: 'First name *' }));
    expect(question).toEqual({
      external_id: 'first_name',
      text: 'First name',
      type: 'short_text',
      required: true,
      options: [],
    });
  });

  it('falls back to a slug when the control has no name or id', () => {
    const question = buildQuestion(raw({ name: '', label: 'Why do you want to work here?' }));
    expect(question.external_id).toBe('why_do_you_want_to_work_here');
  });
});
