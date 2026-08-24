/**
 * Typed client for the backend assistant API.
 *
 * Retry policy, deliberately narrow:
 *   - network failure or 5xx  -> retry with exponential backoff
 *   - 4xx                     -> never retried. A 4xx means the server refused
 *                                this exact request; repeating it is noise at
 *                                best and hammering at worst.
 *   - 401                     -> prints a clear message and aborts the process.
 *
 * The assistant never retries a policy abort either. That rule lives in the
 * runner, because the server has already turned the attempt into a review task.
 */

import type { AssistantConfig } from './config.js';

export interface HealthOut {
  status: string;
  global_automation_enabled: boolean;
  server_time: string;
}

export interface AttachmentOut {
  role: string;
  document_id: string;
  filename: string;
  content_type: string;
  download_path: string;
}

export interface FieldPlan {
  selector_hint: string;
  question_external_id: string;
  label: string;
  value: string;
  type: string;
  required: boolean;
}

export interface TaskOut {
  application_id: string;
  attempt_id: string;
  mode: string;
  may_click_submit: boolean;
  apply_url: string;
  job_title: string;
  company: string;
  connector_key: string;
  fields: FieldPlan[];
  attachments: AttachmentOut[];
  policy: Record<string, unknown>;
  guard_rules: Record<string, unknown>;
}

export interface DiscoveredQuestionPayload {
  external_id: string;
  text: string;
  type: string;
  required: boolean;
  options: string[];
}

export interface ServerAnswer {
  external_id: string;
  value: string;
  type: string;
  required: boolean;
}

export interface UnanswerableQuestion {
  external_id: string;
  text: string;
  required: boolean;
  reason: string;
}

export interface QuestionsOut {
  answers: ServerAnswer[];
  unanswerable: UnanswerableQuestion[];
  must_abort: boolean;
}

export type ResultOutcome = 'submitted' | 'aborted' | 'failed';

export interface ResultIn {
  outcome: ResultOutcome;
  confirmation_number?: string;
  error_message?: string;
  abort_reason?: string;
  guard_findings?: unknown[];
  filled_fields?: unknown[];
  receipt?: Record<string, unknown>;
  screenshot_base64?: string;
  assistant_version?: string;
}

export interface DownloadedDocument {
  filename: string;
  contentType: string;
  bytes: Buffer;
}

export class ApiError extends Error {
  readonly status: number;
  readonly body: string;

  constructor(message: string, status: number, body: string) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.body = body;
  }
}

/** 401 from the backend: the shared secret is wrong or has been rotated. */
export class AuthRejectedError extends ApiError {
  constructor(body: string) {
    super('Assistant token rejected by the backend (HTTP 401).', 401, body);
    this.name = 'AuthRejectedError';
  }
}

const RETRYABLE_ATTEMPTS = 4;
const BASE_BACKOFF_MS = 800;
const REQUEST_TIMEOUT_MS = 30_000;

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => {
    setTimeout(resolve, ms);
  });
}

export type Logger = (message: string) => void;

export class AssistantApi {
  private readonly config: AssistantConfig;
  private readonly log: Logger;

  constructor(config: AssistantConfig, log: Logger = (message) => console.log(message)) {
    this.config = config;
    this.log = log;
  }

  private headers(extra: Record<string, string> = {}): Record<string, string> {
    return {
      'X-Assistant-Token': this.config.token,
      Accept: 'application/json',
      'User-Agent': `${this.config.userAgentSuffix}`,
      ...extra,
    };
  }

  private url(pathname: string): string {
    return `${this.config.apiBaseUrl}${pathname}`;
  }

  private async send(pathname: string, init: RequestInit): Promise<Response> {
    let lastError: unknown = null;

    for (let attempt = 1; attempt <= RETRYABLE_ATTEMPTS; attempt += 1) {
      let response: Response;
      try {
        response = await fetch(this.url(pathname), {
          ...init,
          signal: AbortSignal.timeout(REQUEST_TIMEOUT_MS),
        });
      } catch (error) {
        // Network level failure: server down, DNS, timeout. Safe to retry.
        lastError = error;
        if (attempt === RETRYABLE_ATTEMPTS) break;
        const wait = BASE_BACKOFF_MS * 2 ** (attempt - 1);
        this.log(`  api: ${pathname} unreachable (${describe(error)}); retrying in ${wait}ms`);
        await sleep(wait);
        continue;
      }

      if (response.status === 401) {
        const body = await safeText(response);
        this.log('');
        this.log('  TOKEN REJECTED. The backend returned 401 for X-Assistant-Token.');
        this.log('  Check BROWSER_ASSISTANT_TOKEN in your .env against the backend setting');
        this.log('  and start the assistant again. Nothing was filled or submitted.');
        this.log('');
        throw new AuthRejectedError(body);
      }

      if (response.status >= 400 && response.status < 500) {
        // Never retried: the server refused this request on its merits.
        const body = await safeText(response);
        throw new ApiError(`${init.method ?? 'GET'} ${pathname} failed: HTTP ${response.status}`, response.status, body);
      }

      if (response.status >= 500) {
        const body = await safeText(response);
        lastError = new ApiError(
          `${init.method ?? 'GET'} ${pathname} failed: HTTP ${response.status}`,
          response.status,
          body,
        );
        if (attempt === RETRYABLE_ATTEMPTS) break;
        const wait = BASE_BACKOFF_MS * 2 ** (attempt - 1);
        this.log(`  api: ${pathname} returned ${response.status}; retrying in ${wait}ms`);
        await sleep(wait);
        continue;
      }

      return response;
    }

    if (lastError instanceof ApiError) throw lastError;
    throw new ApiError(
      `${init.method ?? 'GET'} ${pathname} failed after ${RETRYABLE_ATTEMPTS} attempts: ${describe(lastError)}`,
      0,
      '',
    );
  }

  private async getJson<T>(pathname: string): Promise<T> {
    const response = await this.send(pathname, { method: 'GET', headers: this.headers() });
    return (await response.json()) as T;
  }

  private async postJson<T>(pathname: string, payload: unknown): Promise<T> {
    const response = await this.send(pathname, {
      method: 'POST',
      headers: this.headers({ 'Content-Type': 'application/json' }),
      body: JSON.stringify(payload),
    });
    return (await response.json()) as T;
  }

  async health(): Promise<HealthOut> {
    return this.getJson<HealthOut>('/assistant/health');
  }

  /** Claim the next task the policy gate permits, or null when the queue is empty. */
  async nextTask(mode: 'any' | 'auto_submit' = 'any'): Promise<TaskOut | null> {
    return this.getJson<TaskOut | null>(`/assistant/tasks/next?mode=${encodeURIComponent(mode)}`);
  }

  async resolveQuestions(
    applicationId: string,
    questions: DiscoveredQuestionPayload[],
  ): Promise<QuestionsOut> {
    return this.postJson<QuestionsOut>(`/assistant/tasks/${applicationId}/questions`, { questions });
  }

  async reportResult(applicationId: string, payload: ResultIn): Promise<Record<string, unknown>> {
    return this.postJson<Record<string, unknown>>(`/assistant/tasks/${applicationId}/result`, {
      assistant_version: this.config.assistantVersion,
      ...payload,
    });
  }

  async downloadDocument(documentId: string, fallbackFilename: string): Promise<DownloadedDocument> {
    const response = await this.send(`/assistant/documents/${documentId}`, {
      method: 'GET',
      headers: this.headers({ Accept: '*/*' }),
    });
    const disposition = response.headers.get('content-disposition') ?? '';
    const match = /filename="?([^";]+)"?/u.exec(disposition);
    const bytes = Buffer.from(await response.arrayBuffer());
    return {
      filename: match?.[1] ?? fallbackFilename,
      contentType: response.headers.get('content-type') ?? 'application/octet-stream',
      bytes,
    };
  }
}

async function safeText(response: Response): Promise<string> {
  try {
    return (await response.text()).slice(0, 2000);
  } catch {
    return '';
  }
}

export function describe(error: unknown): string {
  if (error instanceof Error) return `${error.name}: ${error.message}`;
  return String(error);
}
