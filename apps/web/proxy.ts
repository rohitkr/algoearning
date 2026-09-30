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
export default clerkMiddleware((_auth, req) => {
  const token = req.nextUrl.searchParams.get("apisession");
  if (token && req.nextUrl.pathname !== "/monitor/market-data") {
    const url = req.nextUrl.clone();
    url.pathname = "/monitor/market-data";
    url.search = `?apisession=${encodeURIComponent(token)}`;
    return NextResponse.redirect(url);
  }
  const host = req.headers.get("host") ?? "";
  if (host.startsWith("monitor.") && req.nextUrl.pathname === "/") {
    const url = req.nextUrl.clone();
    url.pathname = "/monitor";
    return NextResponse.rewrite(url);
  }
});

export const config = {
  matcher: [
    // everything except Next internals and static files
    "/((?!_next|[^?]*\\.(?:html?|css|js(?!on)|jpe?g|webp|png|gif|svg|ttf|woff2?|ico|csv|docx?|xlsx?|zip|webmanifest|txt)).*)",
    "/(api|trpc)(.*)",
    "/__clerk/:path*",
  ],
};
