// Next.js 16 "proxy" (formerly middleware): Clerk reads the session on every request so pages and server code
// can call auth(). Access control is NOT done here by path matching (Clerk's current guidance): every signed-in
// page protects itself with `await auth.protect()` (see lib/session.ts), and the API independently verifies the
// session token on each call.
import { clerkMiddleware } from "@clerk/nextjs/server";
import { type NextRequest, NextResponse } from "next/server";

import { gatePage } from "./lib/gate-page";

// Monitor (the admin panel) also answers on its own host, e.g. monitor.algoearning.com: its home page is the
// panel, and every other path (the panel's own /monitor/... links, sign-in) works as on the main host. Admin
// access is still checked by the panel and by the API; the host only changes the front door.
// ICICI's Breeze login redirects the browser to the app's root with ?apisession=<token> (the Redirect URL registered
// for the Breeze app), and the platform Kite app's login with ?request_token=<token>&status=success (ADR 0021).
// Forward either to Monitor's market-data page, which saves the session, removes the token from the address bar
// and confirms. If the admin is signed out, Clerk's sign-in returns to that same URL afterwards.
// Test environment: when APP_GATE_PASSWORD is set (production on the Mac), every page first shows the team password
// page (lib/gate-page.ts) until that master password is entered, so app.algoearning.com is closed to the public;
// algoearning.com itself only shows the closed-alpha splash (apps/landing). The form posts to /__gate, which sets an
// HttpOnly cookie holding an HMAC of the password (never the password itself) for 30 days: changing the password signs
// everyone out of the gate.
// Behind the password everything starts at HOME: "/" goes there, and it shows the product page (apps/web/landing,
// copied to public/landing.html by scripts/sync-landing.mjs) to anyone signed out and the dashboard to anyone signed
// in. The sign-in pages send a signed-in user there too, and /dashboard sends a signed-out one there.
const HOME = "/testing-dashboard";

const GATE_COOKIE = "ae_gate";
const GATE_DAYS = 30;

function sameText(a: string, b: string): boolean {
  if (a.length !== b.length) return false;
  let diff = 0;
  for (let i = 0; i < a.length; i++) diff |= a.charCodeAt(i) ^ b.charCodeAt(i); // no early exit: constant time
  return diff === 0;
}

async function gateToken(password: string): Promise<string> {
  const enc = new TextEncoder();
  const key = await crypto.subtle.importKey(
    "raw",
    enc.encode(password),
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["sign"],
  );
  const sig = new Uint8Array(await crypto.subtle.sign("HMAC", key, enc.encode("algoearning-gate-v1")));
  return Array.from(sig, (b) => b.toString(16).padStart(2, "0")).join("");
}

/** Only a path on this site: never another host ("//evil.com", "https://..."). */
function safeNext(v: FormDataEntryValue | null): string {
  const s = typeof v === "string" ? v : "/";
  return s.startsWith("/") && !s.startsWith("//") && !s.startsWith("/\\") ? s : "/";
}

function gateResponse(next: string, wrong: boolean): NextResponse {
  return new NextResponse(gatePage(next, wrong), {
    status: 401,
    headers: { "Content-Type": "text/html; charset=utf-8", "Cache-Control": "no-store" },
  });
}

async function gate(req: NextRequest, password: string): Promise<NextResponse | null> {
  const token = await gateToken(password);
  if (req.nextUrl.pathname === "/__gate" && req.method === "POST") {
    const form = await req.formData().catch(() => null);
    const next = safeNext(form?.get("next") ?? null);
    const given = form?.get("password");
    if (typeof given !== "string" || !sameText(await gateToken(given), token)) {
      await new Promise((r) => setTimeout(r, 750)); // slows down guessing
      return gateResponse(next, true);
    }
    const res = NextResponse.redirect(new URL(next, req.url), 303);
    res.cookies.set(GATE_COOKIE, token, {
      httpOnly: true,
      secure: req.nextUrl.protocol === "https:",
      sameSite: "lax",
      path: "/",
      maxAge: GATE_DAYS * 24 * 3600,
    });
    return res;
  }
  if (sameText(req.cookies.get(GATE_COOKIE)?.value ?? "", token)) return null;
  return gateResponse(req.nextUrl.pathname + req.nextUrl.search, false);
}

export default clerkMiddleware(async (auth, req) => {
  const password = process.env.APP_GATE_PASSWORD;
  if (password) {
    const blocked = await gate(req, password);
    if (blocked) return blocked;
  }
  const token = req.nextUrl.searchParams.get("apisession");
  if (token && req.nextUrl.pathname !== "/monitor/market-data") {
    const url = req.nextUrl.clone();
    url.pathname = "/monitor/market-data";
    url.search = `?apisession=${encodeURIComponent(token)}`;
    return NextResponse.redirect(url);
  }
  const kite = req.nextUrl.searchParams.get("request_token");
  if (
    kite &&
    req.nextUrl.searchParams.get("status") === "success" &&
    req.nextUrl.pathname !== "/monitor/market-data"
  ) {
    const url = req.nextUrl.clone();
    url.pathname = "/monitor/market-data";
    url.search = `?request_token=${encodeURIComponent(kite)}&status=success`;
    return NextResponse.redirect(url);
  }
  const { pathname } = req.nextUrl;
  if (![HOME, "/", "/alpha-testing-dashboard", "/dashboard", "/sign-in", "/sign-up"].includes(pathname))
    return;
  const url = req.nextUrl.clone();
  if (pathname === "/" && (req.headers.get("host") ?? "").startsWith("monitor.")) {
    url.pathname = "/monitor";
    return NextResponse.rewrite(url);
  }
  if (pathname === "/" || pathname === "/alpha-testing-dashboard") {
    url.pathname = HOME; // also the entry's earlier name
    return NextResponse.redirect(url);
  }
  const signedIn = Boolean((await auth()).userId);
  if (pathname === HOME) {
    url.pathname = signedIn ? "/dashboard" : "/landing.html";
    return NextResponse.rewrite(url);
  }
  // signed in, the sign-in pages lead to HOME; signed out, so does the dashboard. Other pages still go through
  // sign-in and back, so links from alerts keep working.
  if (signedIn === (pathname === "/dashboard")) return;
  url.pathname = HOME;
  url.search = "";
  return NextResponse.redirect(url);
});

export const config = {
  matcher: [
    // everything except Next internals and static files
    "/((?!_next|[^?]*\\.(?:html?|css|js(?!on)|jpe?g|webp|png|gif|svg|ttf|woff2?|ico|csv|docx?|xlsx?|zip|webmanifest|txt)).*)",
    "/(api|trpc)(.*)",
    "/__clerk/:path*",
    "/landing.html", // the product page sits behind the password too
  ],
};
