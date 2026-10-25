# LLM Insight dashboard image for the opt-in compose `app` profile (Phase 3C).
# Node 22.23.4 exactly (scripts/next.mjs enforces .nvmrc). No .env.local enters the image
# (.dockerignore); the three server-side variables come from compose at run time.
FROM node:22.23.4-bookworm-slim AS build
WORKDIR /app
ENV NEXT_TELEMETRY_DISABLED=1
COPY package.json package-lock.json .npmrc .nvmrc ./
RUN npm ci --no-audit --no-fund
COPY . .
RUN node scripts/next.mjs build

FROM node:22.23.4-bookworm-slim
WORKDIR /app
ENV NODE_ENV=production NEXT_TELEMETRY_DISABLED=1
COPY package.json package-lock.json .npmrc .nvmrc ./
RUN npm ci --omit=dev --no-audit --no-fund && npm cache clean --force
COPY --from=build /app/.next ./.next
COPY --from=build /app/next.config.ts ./next.config.ts
COPY --from=build /app/scripts/next.mjs ./scripts/next.mjs
USER node
# 0.0.0.0 inside the shared network namespace so compose can publish the port; compose binds
# it to 127.0.0.1 on the host only. The Host guard in proxy.ts still refuses non-loopback names.
CMD ["node", "scripts/next.mjs", "start", "--hostname", "0.0.0.0", "--port", "3001"]
