/**
 * Lever.
 *
 * Lever posts a single `name` field rather than first/last, so the server's
 * first_name / last_name answers have nowhere to go on their own. That is not
 * something this module papers over by concatenating strings: it maps the
 * full_name / name questions the form actually asks, and anything unmapped
 * falls through to the generic ladder and, failing that, to review.
 *
 * Additional links are posted as `urls[LinkedIn]`-style names. Those are real
 * Lever field names, but the set varies per posting, so the hints stay broad.
 */

import type { PortalAdapter } from './index.js';

export const LEVER: PortalAdapter = {
  key: 'lever',
  displayName: 'Lever',

  hostPatterns: ['jobs.lever.co', 'jobs.eu.lever.co'],

  submitSelector: '.template-btn-submit, button[type="submit"], input[type="submit"]',

  requiredFormMarkers: [
    'form.application-form, .application-form, form[action*="/apply"]',
    'input[name="email"], input[type="email"]',
  ],

  fieldHints: {
    // Lever's one-field name box. Listed under several ids because the server
    // may key the answer by whichever question text the form showed.
    name: ['input[name="name"]', '#name'],
    full_name: ['input[name="name"]', '#name'],
    email: ['input[name="email"]', '#email', 'input[type="email"]'],
    phone: ['input[name="phone"]', '#phone', 'input[type="tel"]'],
    org: ['input[name="org"]'],
    company: ['input[name="org"]'],
    resume: ['input[name="resume"]', 'input[type="file"][name*="resume"]', 'input[type="file"]'],
    // The bracketed names are Lever's own convention; the attribute-contains
    // fallbacks cover postings that label the same link differently.
    linkedin: ['input[name="urls[LinkedIn]"]', 'input[name*="linkedin" i]'],
    github: ['input[name="urls[GitHub]"]', 'input[name*="github" i]'],
    website: ['input[name="urls[Portfolio]"]', 'input[name*="portfolio" i]', 'input[name*="website" i]'],
  },

  unsupportedMarkers: [
    { selector: 'input[type="password"]', reason: 'login_required' },
    // "Apply with LinkedIn" and similar identity-provider buttons. Using one
    // would mean signing in as the user through a third party, which this
    // assistant does not do even when the button is right there.
    {
      selector: 'a[href*="/oauth"], button[data-provider], a[href*="linkedin.com/oauth"]',
      reason: 'login_required',
    },
  ],
};
