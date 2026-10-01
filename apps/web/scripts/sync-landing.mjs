// The landing page has one source, apps/landing (served as is at algoearning.com). The web app serves the same
// files as its signed-out home page (proxy.ts rewrites "/" to /landing.html), so copy them into public/ before
// `next dev` and `next build`. The copies are git-ignored.
import { copyFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

const from = (f) => fileURLToPath(new URL(`../../landing/${f}`, import.meta.url));
const to = (f) => fileURLToPath(new URL(`../public/${f}`, import.meta.url));

for (const [src, dest] of [
  ["index.html", "landing.html"],
  ["landing.css", "landing.css"],
  ["favicon.svg", "favicon.svg"],
]) {
  copyFileSync(from(src), to(dest));
}
