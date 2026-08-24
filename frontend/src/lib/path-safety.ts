/**
 * Path-segment validation for the API proxy.
 *
 * Lives outside app/ because a Next.js route module may only export the HTTP
 * verb handlers plus the framework's route-config keys; exporting anything else
 * fails the build. Keeping it here also makes it unit testable.
 *
 * The proxy joins these segments onto the trusted API base. A segment that
 * contains a separator, a traversal token, or a control character could escape
 * that base or inject a second line into the request, so each one is checked.
 */
export function isSafeSegment(segment: string): boolean {
  if (segment === '' || segment === '.' || segment === '..') return false
  if (segment.includes('/') || segment.includes('\\')) return false
  // Control characters (including CR, LF and NUL) must never reach the request line.
  for (const character of segment) {
    const code = character.codePointAt(0) ?? 0
    if (code < 0x20 || code === 0x7f) return false
  }
  return true
}

/** True when every segment of a proxied path is safe to append to the API base. */
export function isSafePath(path: readonly string[]): boolean {
  return path.length > 0 && path.every(isSafeSegment)
}
