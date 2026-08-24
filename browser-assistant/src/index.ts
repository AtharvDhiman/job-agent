/**
 * CLI entry point.
 *
 * Prints who it is, what mode it is in, and what it refuses to do - before it
 * touches anything. Then polls, one task at a time, until you stop it.
 */

import { ASSISTANT_NAME, ASSISTANT_VERSION, ConfigError, loadConfig, loadDotEnv, type AssistantConfig } from './config.js';
import { AssistantApi, AuthRejectedError, describe } from './api.js';
import { NEVER_DO } from './core/guards.js';
import { runOnce, type RunOutcome } from './runner.js';

const log = (message: string): void => {
  console.log(message);
};

let shuttingDown = false;
let wake: (() => void) | null = null;
const shutdown = new Promise<void>((resolve) => {
  process.on('SIGINT', () => {
    if (shuttingDown) process.exit(130);
    shuttingDown = true;
    log('');
    log('  Ctrl+C received. Finishing up and closing the browser.');
    if (wake !== null) wake();
    resolve();
  });
});

function sleep(seconds: number): Promise<void> {
  return new Promise((resolve) => {
    const timer = setTimeout(() => {
      wake = null;
      resolve();
    }, seconds * 1000);
    wake = () => {
      clearTimeout(timer);
      wake = null;
      resolve();
    };
  });
}

function banner(config: AssistantConfig): void {
  const mode = config.dryRun ? 'DRY RUN - the submit button will NOT be clicked' : 'LIVE - submit may be clicked';
  log('');
  log('===============================================================');
  log(`  ${ASSISTANT_NAME} v${ASSISTANT_VERSION}`);
  log('  A local, visible browser assistant. It runs on YOUR machine,');
  log('  in a window you can watch, interrupt, and close at any time.');
  log('===============================================================');
  log(`  Mode:          ${mode}`);
  log(`  Backend:       ${config.apiBaseUrl}`);
  log(`  Poll interval: every ${config.pollIntervalSeconds}s while the queue is empty`);
  log(`  Browser:       chromium, headed, slowMo ${config.slowMoMs}ms`);
  log(`  Max runtime:   ${config.maxRuntimeSeconds}s per application`);
  log(`  User-Agent:    real browser UA + "${config.userAgentSuffix}"`);
  log('---------------------------------------------------------------');
  log('  This program will NEVER:');
  for (const item of NEVER_DO) log(`    - ${item}`);
  log('---------------------------------------------------------------');
  log('  Submission is review-first. LinkedIn and Indeed are prohibited');
  log('  for automated submission and cannot be enabled.');
  log('===============================================================');
  log('');
}

async function main(): Promise<number> {
  loadDotEnv();

  let config: AssistantConfig;
  try {
    config = loadConfig();
  } catch (error) {
    if (error instanceof ConfigError) {
      log('');
      log(`  CONFIGURATION ERROR: ${error.message}`);
      log('');
      return 2;
    }
    throw error;
  }

  banner(config);

  const api = new AssistantApi(config, log);
  const ctx = { config, api, log };

  while (!shuttingDown) {
    let outcome: RunOutcome;
    try {
      outcome = await runOnce(ctx);
    } catch (error) {
      if (error instanceof AuthRejectedError) return 1;
      log(`  Poll failed: ${describe(error)}`);
      outcome = 'failed';
    }

    if (outcome === 'handoff' || outcome === 'dry_run') {
      // The browser window belongs to the human now. Polling stops so the
      // assistant cannot claim another task behind their back, and the process
      // stays alive so the window stays open.
      log('  Polling paused while the browser window is open. Press Ctrl+C when you are done.');
      await shutdown;
      break;
    }

    if (outcome === 'idle') {
      log(`  Nothing queued. Next poll in ${config.pollIntervalSeconds}s.`);
    }
    if (shuttingDown) break;
    await sleep(config.pollIntervalSeconds);
  }

  log('  Stopped. Nothing is running in the background.');
  return 0;
}

main()
  .then((code) => {
    process.exitCode = code;
  })
  .catch((error: unknown) => {
    log(`  Fatal: ${describe(error)}`);
    process.exitCode = 1;
  });
