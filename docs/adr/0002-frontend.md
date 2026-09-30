# 0002 Frontend: Next.js, Tailwind, light + dark themes

**Decision.** Next.js (App Router) + React + TypeScript + Tailwind v4, components in `packages/ui`
(shadcn-style: Radix primitives, `cva` variants). Server rendering for the public site; the dashboard is
client-heavy. Live ticks will use a small external store with per-instrument subscriptions so a tick
re-renders only the cells showing that instrument (the pattern proven in the local app).

**Theming.** Light and dark are first-class: every colour is a CSS variable token (`--surface`, `--muted`,
`--profit`, `--loss`, ...) defined for `:root` and `.dark`; components only use token utilities
(`bg-surface`, `text-profit`), never raw colours. `next-themes` stores light / dark / system per browser
and sets the class before paint (no flash). Both themes keep text contrast >= 4.5:1.

**UX references.** Algorooms (app shell, broker cards, deployment flow) and Sensibull (strategy builder,
payoff). Inspiration only; no copied assets or text.
