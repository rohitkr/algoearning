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
// Closed alpha: "/" shows the "closed to the public" splash (apps/landing, copied to public/landing.html by
// scripts/sync-landing.mjs) to anyone signed out, so app.algoearning.com and algoearning.com show the same page and
// nothing links to sign-in. The team's entry is ALPHA_HOME: signed out it opens sign-in and comes back, signed in it
// shows the dashboard. A signed-in user at "/" or a sign-in page goes there; a signed-out one at /dashboard goes "/".
// Sign-up is closed (its page shows the banner; the API refuses accounts it doesn't already have).
const ALPHA_HOME = "/alpha-testing-dashboard";
export default clerkMiddleware(async (auth, req) => {
  const token = req.nextUrl.searchParams.get("apisession");
  if (token && req.nextUrl.pathname !== "/monitor/market-data") {
    const url = req.nextUrl.clone();
    url.pathname = "/monitor/market-data";
    url.search = `?apisession=${encodeURIComponent(token)}`;
    return NextResponse.redirect(url);
  }
  const { pathname } = req.nextUrl;
  if (![ALPHA_HOME, "/", "/dashboard", "/sign-in", "/sign-up"].includes(pathname)) return;
  const url = req.nextUrl.clone();
  if (pathname === "/" && (req.headers.get("host") ?? "").startsWith("monitor.")) {
    url.pathname = "/monitor";
    return NextResponse.rewrite(url);
  }
  const signedIn = Boolean((await auth()).userId);
  if (pathname === ALPHA_HOME) {
    if (signedIn) {
      url.pathname = "/dashboard";
      return NextResponse.rewrite(url);
    }
    url.pathname = "/sign-in";
    url.search = `?redirect_url=${encodeURIComponent(ALPHA_HOME)}`;
    return NextResponse.redirect(url);
  }
  if (pathname === "/" && !signedIn) {
    url.pathname = "/landing.html";
    return NextResponse.rewrite(url);
  }
  // signed in, "/" and the sign-in pages lead to the dashboard; signed out, the dashboard leads to the splash.
  // Other pages still go through sign-in and back, so links from alerts keep working.
  if (signedIn === (pathname === "/dashboard")) return;
  url.pathname = signedIn ? ALPHA_HOME : "/";
  url.search = "";
  return NextResponse.redirect(url);
});

export const config = {
  matcher: [
    // everything except Next internals and static files
    "/((?!_next|[^?]*\\.(?:html?|css|js(?!on)|jpe?g|webp|png|gif|svg|ttf|woff2?|ico|csv|docx?|xlsx?|zip|webmanifest|txt)).*)",
    "/(api|trpc)(.*)",
    "/__clerk/:path*",
  ],
};
