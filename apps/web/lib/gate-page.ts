// The page behind proxy.ts's master password: browsers show it when the password pop-up is cancelled or the password
// is wrong. Self-contained (inline styles and script), because nothing else on the site loads without the password.
// Same look and theme key as the closed-alpha splash on algoearning.com (apps/landing).
export const GATE_PAGE = `<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<meta name="robots" content="noindex, nofollow" />
<meta name="color-scheme" content="dark light" />
<title>AlgoEarning</title>
<script>
  (function () {
    var choice = "dark";
    try { choice = localStorage.getItem("theme") || "dark"; } catch (e) {}
    var light = choice === "light" || (choice === "system" && matchMedia("(prefers-color-scheme: light)").matches);
    document.documentElement.dataset.theme = light ? "light" : "dark";
  })();
</script>
<style>
  :root { --bg: #0b0c10; --surface: #16171c; --fg: #eef0f5; --muted: #9aa3b4; --border: #2a2d36; --primary: #2f63e0;
    --primary-hover: #3b6ff0; --glow: rgb(47 99 224 / 0.28); color-scheme: dark; }
  html[data-theme="light"] { --bg: #f6f7fb; --surface: #ffffff; --fg: #0f172a; --muted: #566074; --border: #e2e5ee;
    --primary: #2459e0; --primary-hover: #1d4bc2; --glow: rgb(36 89 224 / 0.16); color-scheme: light; }
  * { box-sizing: border-box; }
  body { margin: 0; min-height: 100dvh; display: grid; place-items: center; padding: 24px 16px; color: var(--fg);
    background: radial-gradient(60rem 30rem at 50% -10%, var(--glow), transparent 70%), var(--bg);
    font-family: Inter, system-ui, -apple-system, "Segoe UI", Roboto, sans-serif; }
  .card { width: 100%; max-width: 400px; padding: 36px 28px; text-align: center; background: var(--surface);
    border: 1px solid var(--border); border-radius: 16px; box-shadow: 0 24px 60px -24px rgb(0 0 0 / 0.5); }
  .mark { display: inline-grid; place-items: center; width: 52px; height: 52px; border-radius: 14px;
    background: var(--primary); color: #fff; }
  .mark svg { width: 28px; height: 28px; }
  h1 { margin: 18px 0 6px; font-size: 24px; font-weight: 700; letter-spacing: -0.01em; }
  .lock { display: inline-flex; align-items: center; gap: 6px; margin: 0 0 14px; padding: 4px 10px; font-size: 12px;
    font-weight: 600; color: var(--muted); border: 1px solid var(--border); border-radius: 999px; }
  .lock svg { width: 12px; height: 12px; }
  p { margin: 0; color: var(--muted); font-size: 15px; line-height: 1.55; }
  button { margin-top: 24px; width: 100%; padding: 11px 16px; font: inherit; font-weight: 600; color: #fff;
    background: var(--primary); border: 0; border-radius: 10px; cursor: pointer; }
  button:hover { background: var(--primary-hover); }
  button:focus-visible { outline: 2px solid var(--primary); outline-offset: 3px; }
  .hint { margin-top: 14px; font-size: 13px; }
</style>
</head>
<body>
<main class="card">
  <span class="mark" aria-hidden="true">
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round"
      stroke-linejoin="round"><path d="M3 17l6-6 4 4 8-8" /><path d="M15 7h6v6" /></svg>
  </span>
  <h1>AlgoEarning</h1>
  <span class="lock">
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round"
      aria-hidden="true"><rect x="4" y="11" width="16" height="10" rx="2" /><path d="M8 11V7a4 4 0 0 1 8 0v4" /></svg>
    Team access only
  </span>
  <p>Internal Alpha Test Environment. Closed to the public.</p>
  <button type="button" onclick="location.reload()">Enter the team password</button>
  <p class="hint">Any username works. Ask the team for the password.</p>
</main>
</body>
</html>
`;
