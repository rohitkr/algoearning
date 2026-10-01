// app.algoearning.com's signed-out home page is the full product page in apps/web/landing (proxy.ts rewrites "/" to
// /landing.html), while algoearning.com (apps/landing) only shows the closed-alpha splash. Copy the page into
// public/ before `next dev` and `next build`; the favicon is shared with apps/landing. The copies are git-ignored.
import { copyFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

const from = (f) => fileURLToPath(new URL(`../${f}`, import.meta.url));
const to = (f) => fileURLToPath(new URL(`../public/${f}`, import.meta.url));

for (const [src, dest] of [
  ["landing/index.html", "landing.html"],
  ["landing/landing.css", "landing.css"],
  ["../landing/favicon.svg", "favicon.svg"],
]) {
  copyFileSync(from(src), to(dest));
}
