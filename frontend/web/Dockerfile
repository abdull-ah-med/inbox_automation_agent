# Multi-stage production image using Next.js `output: "standalone"`.
# Pattern from the official Next.js with-docker example + Docker docs:
# https://github.com/vercel/next.js/tree/canary/examples/with-docker
# https://docs.docker.com/guides/nextjs/containerize/
# https://nextjs.org/docs/app/api-reference/config/next-config-js/output

FROM node:22-slim AS base
WORKDIR /app
ENV NEXT_TELEMETRY_DISABLED=1

FROM base AS deps
COPY package.json package-lock.json ./
RUN npm ci

FROM base AS build
ARG NEXT_PUBLIC_API_BASE_URL
ENV NEXT_PUBLIC_API_BASE_URL=$NEXT_PUBLIC_API_BASE_URL
ENV NODE_ENV=production
COPY --from=deps /app/node_modules ./node_modules
COPY . .
RUN npm run build

FROM base AS production
ENV NODE_ENV=production
ENV PORT=3000
ENV HOSTNAME=0.0.0.0
RUN useradd --create-home --uid 10001 --shell /usr/sbin/nologin appuser

COPY --from=build --chown=appuser:appuser /app/public ./public
RUN mkdir .next && chown appuser:appuser .next

# Trace-only runtime (no full node_modules / no devDependencies).
COPY --from=build --chown=appuser:appuser /app/.next/standalone ./
COPY --from=build --chown=appuser:appuser /app/.next/static ./.next/static

USER appuser
EXPOSE 3000
CMD ["node", "server.js"]
