/**
 * Ashby.
 *
 * Ashby renders its application as a React form. The DOM is generated, class
 * names are hashed and can change between deploys, and fields mount after the
 * initial paint. Nothing here may depend on a hashed class name, so the hints
 * lean on the two things Ashby does keep stable: its `_systemfield_*` input
 * names for the built-in questions, and the accessible name of each control.
 *
 * Where even those are uncertain the hint is deliberately broad and the generic
 * label-based ladder in core/fill.ts does the real work. An unresolved field is
 * a review task, which is the correct outcome; a field resolved by guessing at
 * a hashed selector is not.
 */

import type { PortalAdapter } from './index.js';

export const ASHBY: PortalAdapter = {
  key: 'ashby',
  displayName: 'Ashby',

  hostPatterns: ['jobs.ashbyhq.com', 'ashbyhq.com'],

  submitSelector: 'button[type="submit"]',

  requiredFormMarkers: [
    // Broad by necessity: Ashby exposes no stable form id. A marketing or
    // job-description page has no form and no email field, so the pair still
    // separates a real application from everything else on the host.
    'form',
    'input[type="email"], input[name*="email" i], input[aria-label*="email" i]',
  ],

  fieldHints: {
    // Ashby posts its built-in questions under _systemfield_ names. Kept first
    // because when they are present they are exact; the aria-label fallbacks
    // cover the case where they are not.
    name: ['input[name="_systemfield_name"]', 'input[aria-label="Name"]'],
    full_name: ['input[name="_systemfield_name"]', 'input[aria-label="Name"]'],
    first_name: ['input[aria-label*="first name" i]', 'input[autocomplete="given-name"]'],
    last_name: ['input[aria-label*="last name" i]', 'input[autocomplete="family-name"]'],
    email: [
      'input[name="_systemfield_email"]',
      'input[type="email"]',
      'input[aria-label*="email" i]',
    ],
    phone: ['input[name="_systemfield_phone"]', 'input[type="tel"]', 'input[aria-label*="phone" i]'],
    resume: [
      'input[name="_systemfield_resume"]',
      'input[type="file"][name*="resume" i]',
      'input[type="file"]',
    ],
    linkedin: ['input[aria-label*="linkedin" i]', 'input[name*="linkedin" i]'],
    website: ['input[aria-label*="website" i]', 'input[name*="website" i]'],
  },

  unsupportedMarkers: [
    { selector: 'input[type="password"]', reason: 'login_required' },
    // Ashby can front a posting with a candidate-portal sign-in. Matched on the
    // href rather than on button text so a translated page is still caught.
    { selector: 'a[href*="/login"], a[href*="/signin"], a[href*="/oauth"]', reason: 'login_required' },
  ],
};
