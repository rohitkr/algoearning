// Next.js 16 "proxy" (formerly middleware): Clerk reads the session on every request so pages and server code
// can call auth(). Access control is NOT done here by path matching (Clerk's current guidance): every signed-in
// page protects itself with `await auth.protect()` (see lib/session.ts), and the API independently verifies the
// session token on each call.
import { clerkMiddleware } from "@clerk/nextjs/server";
import { NextResponse } from "next/server";

// Monitor (the admin panel) also answers on its own host, e.g. monitor.algoearning.com: its home page is the
// panel, and every other path (the panel's own /monitor/... links, sign-in) works as on the main host. Admin
// access is still checked by the panel and by the API; the host only changes the front door.
// ICICI's Breeze login redirects the browser to the app's root with ?apisession=<token> (the Redirect URL registered
// for the Breeze app). Forward it to Monitor's market-data page, which saves the session, removes the token from
// the address bar and confirms. If the admin is signed out, Clerk's sign-in returns to that same URL afterwards.
// Test environment: when APP_GATE_PASSWORD is set (production on the Mac), every page asks for that master password
// in the browser's sign-in pop-up (HTTP Basic auth, any username) before anything else, so app.algoearning.com is
// closed to the public; algoearning.com itself only shows the closed-alpha splash (apps/landing).
// Behind the password, "/" (and /test) is the product page (apps/web/landing, copied to public/landing.html by
// scripts/sync-landing.mjs) for anyone signed out, and HOME is the app's entry: signed out it opens sign-in and
// comes back, signed in it shows the dashboard. A signed-in user at "/", /test or a sign-in page goes to HOME; a
// signed-out one at /dashboard goes to "/".
const HOME = "/testing-dashboard";
const LANDING = ["/", "/test"];

function sameText(a: string, b: string): boolean {
  if (a.length !== b.length) return false;
  let diff = 0;
  for (let i = 0; i < a.length; i++) diff |= a.charCodeAt(i) ^ b.charCodeAt(i); // no early exit: constant time
  return diff === 0;
}

function passwordOk(header: string | null, password: string): boolean {
  if (!header?.startsWith("Basic ")) return false;
  let decoded = "";
  try {
    decoded = atob(header.slice(6).trim());
  } catch {
    return false;
  }
  return sameText(decoded.slice(decoded.indexOf(":") + 1), password);
}

export default clerkMiddleware(async (auth, req) => {
  const password = process.env.APP_GATE_PASSWORD;
  if (password && !passwordOk(req.headers.get("authorization"), password)) {
    return new NextResponse("Internal Alpha Test Environment. Closed to the public.", {
      status: 401,
      headers: { "WWW-Authenticate": 'Basic realm="AlgoEarning test", charset="UTF-8"' },
    });
  }
  const token = req.nextUrl.searchParams.get("apisession");
  if (token && req.nextUrl.pathname !== "/monitor/market-data") {
    const url = req.nextUrl.clone();
    url.pathname = "/monitor/market-data";
    url.search = `?apisession=${encodeURIComponent(token)}`;
    return NextResponse.redirect(url);
  }
  const { pathname } = req.nextUrl;
  if (
    ![HOME, "/alpha-testing-dashboard", ...LANDING, "/dashboard", "/sign-in", "/sign-up"].includes(pathname)
  )
    return;
  const url = req.nextUrl.clone();
  if (pathname === "/" && (req.headers.get("host") ?? "").startsWith("monitor.")) {
    url.pathname = "/monitor";
    return NextResponse.rewrite(url);
  }
  if (pathname === "/alpha-testing-dashboard") {
    url.pathname = HOME; // the earlier name of the entry
    return NextResponse.redirect(url);
  }
  const signedIn = Boolean((await auth()).userId);
  if (pathname === HOME) {
    if (signedIn) {
      url.pathname = "/dashboard";
      return NextResponse.rewrite(url);
    }
    url.pathname = "/sign-in";
    url.search = `?redirect_url=${encodeURIComponent(HOME)}`;
    return NextResponse.redirect(url);
  }
  if (LANDING.includes(pathname) && !signedIn) {
    url.pathname = "/landing.html";
    return NextResponse.rewrite(url);
  }
  // signed in, the landing and sign-in pages lead to the dashboard; signed out, the dashboard leads to the landing
  // page. Other pages still go through sign-in and back, so links from alerts keep working.
  if (signedIn === (pathname === "/dashboard")) return;
  url.pathname = signedIn ? HOME : "/";
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
