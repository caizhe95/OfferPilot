# OfferPilot Lite - Web UI
FROM node:20-alpine AS builder

WORKDIR /app
RUN apk add --no-cache libc6-compat
ARG API_BASE_URL=http://api:8000
ENV API_BASE_URL=${API_BASE_URL}
COPY src/web/package.json src/web/package-lock.json ./
RUN npm ci

RUN mkdir -p public
COPY src/web/ ./
RUN npm run build

FROM node:20-alpine AS runner
WORKDIR /app
RUN apk add --no-cache libc6-compat
ENV NODE_ENV=production
ENV HOSTNAME=0.0.0.0

COPY --from=builder /app/public ./public
COPY --from=builder /app/.next/standalone ./
COPY --from=builder /app/.next/static ./.next/static

EXPOSE 3000

HEALTHCHECK --interval=10s --timeout=5s --start-period=10s --retries=5 \
  CMD node -e "fetch('http://127.0.0.1:3000').then((response) => process.exit(response.ok ? 0 : 1)).catch(() => process.exit(1))"

CMD ["node", "server.js"]
