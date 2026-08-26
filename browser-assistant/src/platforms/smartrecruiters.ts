/**
 * SmartRecruiters.
 *
 * Two front ends share the brand: the standard job page on
 * jobs.smartrecruiters.com and the customer-branded career site on
 * careers.smartrecruiters.com. Both post the same camelCased core fields, which
 * is the one thing worth encoding here; the rest of the markup is a generated
 * single-page app and is matched only through `data-test` hooks with plain
 * fallbacks behind them.
 */

import type { PortalAdapter } from './index.js';

export const SMARTRECRUITERS: PortalAdapter = {
  key: 'smartrecruiters',
  displayName: 'SmartRecruiters',

  hostPatterns: ['jobs.smartrecruiters.com', 'careers.smartrecruiters.com', 'api.smartrecruiters.com'],

  submitSelector: 'button[type="submit"], #submit-app, [data-test="submit-application"]',

  requiredFormMarkers: [
    'form, #application-form, [data-test="application-form"]',
    'input[type="email"], input[name="email"]',
  ],

  fieldHints: {
    first_name: ['input[name="firstName"]', '#firstName', 'input[autocomplete="given-name"]'],
    last_name: ['input[name="lastName"]', '#lastName', 'input[autocomplete="family-name"]'],
    email: ['input[name="email"]', '#email', 'input[type="email"]'],
    phone: ['input[name="phoneNumber"]', 'input[name="phone"]', 'input[type="tel"]'],
    resume: ['input[type="file"][name*="resume" i]', 'input[type="file"]'],
    cover_letter: ['input[type="file"][name*="cover" i]', 'textarea[name*="cover" i]'],
    linkedin: ['input[name*="linkedin" i]'],
    website: ['input[name*="web" i]', 'input[name*="portfolio" i]'],
    location: ['input[name*="location" i]', 'input[name="city"]'],
  },

  unsupportedMarkers: [
    { selector: 'input[type="password"]', reason: 'login_required' },
    // "Apply with LinkedIn / Google / Xing" and the returning-candidate portal.
    // All of them are account gates; none of them are ours to walk through.
    {
      selector: '[data-test*="social"], a[href*="/oauth"], a[href*="/login"], button[data-provider]',
      reason: 'login_required',
    },
  ],
};
