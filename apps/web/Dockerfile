# ---- deps ----
FROM node:20-alpine AS deps
WORKDIR /app
ARG NPM_REGISTRY=https://registry.npmjs.org
COPY package.json package-lock.json* ./
RUN npm config set registry "$NPM_REGISTRY" && (npm ci || npm install)

# ---- build ----
FROM node:20-alpine AS builder
WORKDIR /app
ARG NEXT_PUBLIC_API_BASE_URL=
ENV NEXT_PUBLIC_API_BASE_URL=$NEXT_PUBLIC_API_BASE_URL
ENV NEXT_TELEMETRY_DISABLED=1
COPY --from=deps /app/node_modules ./node_modules
COPY . .
RUN npm run build

# ---- runtime ----
FROM node:20-alpine AS runner
WORKDIR /app
ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    PORT=3000 \
    HOSTNAME=0.0.0.0
RUN addgroup -g 1001 -S nodejs && adduser -S nextjs -u 1001
COPY --from=builder /app/public ./public
COPY --from=builder --chown=nextjs:nodejs /app/.next/standalone ./
COPY --from=builder --chown=nextjs:nodejs /app/.next/static ./.next/static
USER nextjs
EXPOSE 3000
CMD ["node", "server.js"]
