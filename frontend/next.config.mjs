const isDev = process.env.NODE_ENV !== 'production'

/**
 * Content-Security-Policy.
 *
 * Production is strict: no `eval`, no remote script or style origins, no
 * framing, and `connect-src 'self'` so the browser can only talk to this origin.
 * The browser never holds an API token anyway (sessions are httpOnly cookies
 * read by the server-side proxy in src/app/api/proxy), so there is nothing for
 * injected script to exfiltrate even if it ran.
 *
 * Development adds 'unsafe-eval' because Next.js Fast Refresh and webpack's
 * eval-based source maps do exactly that. Without it `next dev` serves a page
 * whose JavaScript never boots: the form renders but no handler is attached.
 * `next build` needs no such thing, so the shipped policy stays strict.
 */
const scriptSrc = isDev
  ? "script-src 'self' 'unsafe-inline' 'unsafe-eval'"
  : "script-src 'self' 'unsafe-inline'"

const contentSecurityPolicy = [
  "default-src 'self'",
  scriptSrc,
  "style-src 'self' 'unsafe-inline'",
  "img-src 'self' data:",
  "font-src 'self' data:",
  // The dev server needs a websocket back to itself for hot reload.
  isDev ? "connect-src 'self' ws: wss:" : "connect-src 'self'",
  "object-src 'none'",
  "frame-ancestors 'none'",
  "base-uri 'self'",
  "form-action 'self'",
].join('; ')

/** @type {import('next').NextConfig} */
const nextConfig = {
  // Emits .next/standalone with a traced server.js, which is what the
  // frontend Dockerfile runner stage copies. See frontend/Dockerfile.
  output: 'standalone',
  reactStrictMode: true,
  poweredByHeader: false,
  async headers() {
    return [
      {
        source: '/:path*',
        headers: [
          { key: 'X-Content-Type-Options', value: 'nosniff' },
          { key: 'X-Frame-Options', value: 'DENY' },
          { key: 'Referrer-Policy', value: 'no-referrer' },
          { key: 'Permissions-Policy', value: 'geolocation=(), microphone=(), camera=()' },
          { key: 'Content-Security-Policy', value: contentSecurityPolicy },
        ],
      },
    ]
  },
}

export default nextConfig
