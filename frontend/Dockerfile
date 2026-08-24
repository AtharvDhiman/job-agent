# =============================================================================
#  Frontend image - Next.js 14 App Router, built in "standalone" output mode
#  (see next.config.mjs) so the runtime stage carries only the traced server
#  and its traced node_modules subset instead of the whole dependency tree.
#
#  deps -> builder -> runner
# =============================================================================

# --- dependency stage --------------------------------------------------------
FROM node:20-alpine AS deps

WORKDIR /app

# The lockfile glob is optional so the image still builds before the first
# `npm install` has been committed. Use `npm ci` whenever a lockfile exists.
COPY package.json package-lock.json* ./
RUN if [ -f package-lock.json ]; then npm ci; else npm install; fi

# --- build stage -------------------------------------------------------------
FROM node:20-alpine AS builder

WORKDIR /app

ENV NEXT_TELEMETRY_DISABLED=1

COPY --from=deps /app/node_modules ./node_modules
COPY . .

# NEXT_PUBLIC_* values are inlined at build time. The default points at a local
# API; override with --build-arg for a deployed origin.
ARG NEXT_PUBLIC_API_BASE_URL=http://localhost:8000/api/v1
ENV NEXT_PUBLIC_API_BASE_URL=$NEXT_PUBLIC_API_BASE_URL

RUN npm run build

# --- runtime stage -----------------------------------------------------------
FROM node:20-alpine AS runner

WORKDIR /app

ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    PORT=3000 \
    HOSTNAME=0.0.0.0

RUN addgroup --system --gid 10001 nodejs \
    && adduser --system --uid 10001 --ingroup nodejs nextjs

# Static assets and the traced standalone server.
COPY --from=builder --chown=nextjs:nodejs /app/public ./public
COPY --from=builder --chown=nextjs:nodejs /app/.next/standalone ./
COPY --from=builder --chown=nextjs:nodejs /app/.next/static ./.next/static

USER nextjs

EXPOSE 3000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD node -e "require('http').get({host:'127.0.0.1',port:3000,path:'/',timeout:4000},r=>process.exit(r.statusCode<500?0:1)).on('error',()=>process.exit(1))"

CMD ["node", "server.js"]
