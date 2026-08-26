/**
 * Workable.
 *
 * Workable's hosted application lives on apply.workable.com and names its core
 * inputs without separators - `firstname`, `lastname` - which is easy to get
 * subtly wrong, so both the joined and the underscored spellings are listed.
 * The surrounding markup is a single-page app keyed by `data-ui` attributes;
 * those are more stable than its class names but are still a product detail we
 * do not control, so they are never the only hint.
 */

import type { PortalAdapter } from './index.js';

export const WORKABLE: PortalAdapter = {
  key: 'workable',
  displayName: 'Workable',

  hostPatterns: ['apply.workable.com', 'jobs.workable.com'],

  submitSelector: 'button[type="submit"], [data-ui="submit-application"]',

  requiredFormMarkers: [
    'form, [data-ui="application-form"]',
    'input[type="email"], input[name="email"]',
  ],

  fieldHints: {
    first_name: ['input[name="firstname"]', 'input[name="first_name"]', 'input[autocomplete="given-name"]'],
    last_name: ['input[name="lastname"]', 'input[name="last_name"]', 'input[autocomplete="family-name"]'],
    email: ['input[name="email"]', 'input[type="email"]'],
    phone: ['input[name="phone"]', 'input[type="tel"]'],
    resume: ['input[type="file"][name*="resume" i]', 'input[type="file"]'],
    cover_letter: ['input[type="file"][name*="cover" i]', 'textarea[name*="cover" i]'],
    linkedin: ['input[name*="linkedin" i]'],
    website: ['input[name*="website" i]', 'input[name*="portfolio" i]'],
    location: ['input[name="address"]', 'input[name*="location" i]'],
  },

  unsupportedMarkers: [
    { selector: 'input[type="password"]', reason: 'login_required' },
    // Workable offers social sign-in on some accounts to prefill the form from
    // a profile. Prefilling from an account we would have to log into is not a
    // shortcut this assistant takes.
    {
      selector: '[data-ui*="social-login"], a[href*="/oauth"], button[data-provider]',
      reason: 'login_required',
    },
  ],
};
