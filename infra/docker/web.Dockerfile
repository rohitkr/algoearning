FROM node:22-slim AS build
RUN npm install -g pnpm@12.6.0
WORKDIR /repo
COPY . .
RUN pnpm install --frozen-lockfile && pnpm --filter @algoearning/web build

FROM node:22-slim
ENV NODE_ENV=production PORT=3000 HOSTNAME=0.0.0.0
WORKDIR /app
COPY --from=build /repo/apps/web/.next/standalone ./
COPY --from=build /repo/apps/web/.next/static ./apps/web/.next/static
COPY --from=build /repo/apps/web/public ./apps/web/public
USER node
EXPOSE 3000
CMD ["node", "apps/web/server.js"]
