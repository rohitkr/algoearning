# 0001 Monorepo: pnpm + Turborepo, uv workspace

**Context.** One product with a TypeScript web app and Python services that share contracts.

**Decision.** One repository. TypeScript: pnpm workspaces + Turborepo (`apps/web`, `packages/ui`,
`packages/shared`, `packages/config`, `packages/api-types`). Python: a uv workspace (`apps/api`,
`apps/engine`, `apps/worker`, `packages/py-*`). `make` is the single entry point.
Web types for the API are generated from FastAPI's OpenAPI document; CI fails if they drift.

**Consequences.** One PR can change API + UI together; two package managers, both fast and locked.
