/**
 * Greenhouse.
 *
 * Two generations of the same product are in the wild at once: the long-lived
 * server-rendered board on boards.greenhouse.io, whose form is `#application_form`
 * with plainly-named inputs, and the newer job-boards.greenhouse.io front end,
 * which keeps the field names but not always the ids. Every hint below lists
 * both shapes, most specific first, and the generic locator ladder in
 * core/fill.ts still runs underneath.
 */

import type { PortalAdapter } from './index.js';

export const GREENHOUSE: PortalAdapter = {
  key: 'greenhouse',
  displayName: 'Greenhouse',

  hostPatterns: [
    'boards.greenhouse.io',
    'job-boards.greenhouse.io',
    'boards-api.greenhouse.io',
    // Greenhouse runs an EU-resident board on its own subdomain.
    'job-boards.eu.greenhouse.io',
    'boards.eu.greenhouse.io',
  ],

  // Older boards ship `#submit_app`; the newer front end uses an ordinary
  // submit button. The runner is what clicks this, and only after the backend
  // policy gate has said it may.
  submitSelector: '#submit_app, form#application_form button[type="submit"], button[type="submit"]',

  requiredFormMarkers: [
    // The application form itself. An expired posting or a job description page
    // on the same host has none of these and is refused.
    'form#application_form, form#application-form, form[action*="/applications"], form[action*="/apply"]',
    // Every Greenhouse application collects an email address. If there is no
    // email field, this is not the form we think it is.
    '#email, input[name="email"], input[name="job_application[email]"], input[type="email"]',
  ],

  fieldHints: {
    first_name: [
      '#first_name',
      'input[name="first_name"]',
      'input[name="job_application[first_name]"]',
      'input[autocomplete="given-name"]',
    ],
    last_name: [
      '#last_name',
      'input[name="last_name"]',
      'input[name="job_application[last_name]"]',
      'input[autocomplete="family-name"]',
    ],
    email: ['#email', 'input[name="email"]', 'input[name="job_application[email]"]', 'input[type="email"]'],
    phone: ['#phone', 'input[name="phone"]', 'input[name="job_application[phone]"]', 'input[type="tel"]'],
    // Greenhouse offers paste-or-upload for documents. Only the file input is
    // targeted: the paste box would need text we do not have.
    resume: ['#resume', 'input[type="file"][name*="resume"]', 'input[type="file"]'],
    cover_letter: ['#cover_letter', 'input[type="file"][name*="cover"]'],
    // Custom URL questions are ordinary inputs whose name carries the label.
    // Broad on purpose: the exact suffix differs per board.
    linkedin: ['input[name*="linkedin" i]', 'input[id*="linkedin" i]'],
    website: ['input[name*="website" i]', 'input[id*="website" i]'],
  },

  unsupportedMarkers: [
    // A password box on an application form means an account is being asked
    // for. This assistant holds no credentials and never types any.
    { selector: 'input[type="password"]', reason: 'login_required' },
    // A Rails-shaped sign-in form, which is what a redirect into the candidate
    // account area looks like. Broad rather than precise: a false positive here
    // costs one review task, a false negative costs a login attempt we promised
    // never to make.
    { selector: 'form[action*="sign_in"], form[action*="/users/session"]', reason: 'login_required' },
  ],
};
