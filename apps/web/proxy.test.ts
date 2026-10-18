import { describe, expect, it, vi } from "vitest";

// the Clerk wrapper only forwards the callback: test the callback itself
type Auth = () => Promise<{ userId: string | null }>;
let handler: (auth: Auth, req: unknown) => Promise<Response | undefined>;
vi.mock("@clerk/nextjs/server", () => ({ clerkMiddleware: (fn: typeof handler) => (handler = fn) }));

const req = (url: string, host = "localhost:3000") => {
  const u = new URL(`http://${host}${url}`);
  return { nextUrl: Object.assign(u, { clone: () => new URL(u) }), headers: new Headers({ host }) };
};
const signedIn: Auth = async () => ({ userId: "user_1" });
const signedOut: Auth = async () => ({ userId: null });

describe("proxy", () => {
  it("forwards ICICI's ?apisession= redirect to Monitor's market-data page", async () => {
    await import("./proxy");
    const r = (await handler(signedIn, req("/?apisession=57212559"))) as Response;
    expect(r.status).toBe(307);
    expect(r.headers.get("location")).toBe("http://localhost:3000/monitor/market-data?apisession=57212559");
  });

  it("forwards the platform Kite login's redirect to Monitor's market-data page", async () => {
    await import("./proxy");
    const r = (await handler(
      signedIn,
      req("/?action=login&type=login&status=success&request_token=ab12"),
    )) as Response;
    expect(r.headers.get("location")).toBe(
      "http://localhost:3000/monitor/market-data?request_token=ab12&status=success",
    );
    expect(
      await handler(signedIn, req("/monitor/market-data?request_token=ab12&status=success")),
    ).toBeUndefined();
  });

  it("sends / to the testing dashboard", async () => {
    await import("./proxy");
    for (const who of [signedIn, signedOut]) {
      const r = (await handler(who, req("/", "app.algoearning.com"))) as Response;
      expect(r.status).toBe(307);
      expect(r.headers.get("location")).toBe("http://app.algoearning.com/testing-dashboard");
    }
  });

  it("shows the dashboard at the testing entry when signed in, keeping the address", async () => {
    await import("./proxy");
    const r = (await handler(signedIn, req("/testing-dashboard", "app.algoearning.com"))) as Response;
    expect(r.headers.get("x-middleware-rewrite")).toBe("http://app.algoearning.com/dashboard");
  });

  it("shows the product page at the testing entry when signed out, keeping the address", async () => {
    await import("./proxy");
    const r = (await handler(signedOut, req("/testing-dashboard"))) as Response;
    expect(r.headers.get("x-middleware-rewrite")).toBe("http://localhost:3000/landing.html");
  });

  it("opens Monitor at / on the monitor host, signed in or not", async () => {
    await import("./proxy");
    const r = (await handler(signedIn, req("/", "monitor.algoearning.com"))) as Response;
    expect(r.headers.get("x-middleware-rewrite")).toBe("http://monitor.algoearning.com/monitor");
  });

  it("shows the product page instead of sign-in when a signed-out visitor opens the dashboard", async () => {
    await import("./proxy");
    const r = (await handler(signedOut, req("/dashboard", "app.algoearning.com"))) as Response;
    expect(r.status).toBe(307);
    expect(r.headers.get("location")).toBe("http://app.algoearning.com/testing-dashboard");
  });

  it("sends a signed-in user from the sign-in and sign-up pages to the testing dashboard", async () => {
    await import("./proxy");
    for (const page of ["/sign-in", "/sign-up"]) {
      const r = (await handler(signedIn, req(page))) as Response;
      expect(r.headers.get("location")).toBe("http://localhost:3000/testing-dashboard");
    }
  });

  it("sends the old /alpha-testing-dashboard link to the new one", async () => {
    await import("./proxy");
    const r = (await handler(signedIn, req("/alpha-testing-dashboard"))) as Response;
    expect(r.headers.get("location")).toBe("http://localhost:3000/testing-dashboard");
  });

  it("asks for the master password on its own page, then remembers it in a cookie", async () => {
    const { NextRequest } = await import("next/server");
    await import("./proxy");
    vi.stubEnv("APP_GATE_PASSWORD", "open-sesame");
    try {
      const ask = (await handler(
        signedIn,
        new NextRequest("http://localhost:3000/runs/abc?x=1"),
      )) as Response;
      expect(ask.status).toBe(401);
      expect(ask.headers.get("www-authenticate")).toBeNull(); // no browser pop-up
      const page = await ask.text();
      expect(page).toContain('action="/__gate"');
      expect(page).toContain('value="/runs/abc?x=1"');

      const post = (password: string, next = "/runs/abc?x=1") =>
        new NextRequest("http://localhost:3000/__gate", {
          method: "POST",
          body: new URLSearchParams({ password, next }),
          headers: { "content-type": "application/x-www-form-urlencoded" },
        });
      const wrong = (await handler(signedOut, post("wrong"))) as Response;
      expect(wrong.status).toBe(401);
      expect(await wrong.text()).toContain("not right");

      const ok = (await handler(signedOut, post("open-sesame"))) as Response;
      expect(ok.status).toBe(303);
      expect(ok.headers.get("location")).toBe("http://localhost:3000/runs/abc?x=1");
      const cookie = ok.headers.get("set-cookie") ?? "";
      expect(cookie).toMatch(/^ae_gate=[0-9a-f]{64};/);
      expect(cookie).toContain("HttpOnly");
      expect(cookie).not.toContain("open-sesame");

      const evil = (await handler(signedOut, post("open-sesame", "//evil.com"))) as Response;
      expect(evil.headers.get("location")).toBe("http://localhost:3000/");

      const value = cookie.split(";")[0] ?? "";
      const back = new NextRequest("http://localhost:3000/testing-dashboard", { headers: { cookie: value } });
      const r = (await handler(signedOut, back)) as Response;
      expect(r.headers.get("x-middleware-rewrite")).toBe("http://localhost:3000/landing.html");
    } finally {
      vi.unstubAllEnvs();
    }
  });

  it("leaves every other page alone", async () => {
    await import("./proxy");
    expect(await handler(signedOut, req("/monitor/market-data?apisession=1"))).toBeUndefined();
    expect(await handler(signedIn, req("/dashboard"))).toBeUndefined();
    expect(await handler(signedOut, req("/sign-in"))).toBeUndefined();
    expect(await handler(signedOut, req("/sign-in/factor-one"))).toBeUndefined();
    expect(await handler(signedOut, req("/runs/abc"))).toBeUndefined(); // sign-in and back, for links from alerts
  });
});
