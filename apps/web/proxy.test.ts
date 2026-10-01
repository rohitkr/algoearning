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

  it("sends a signed-in user from / to the testing dashboard", async () => {
    await import("./proxy");
    const r = (await handler(signedIn, req("/", "app.algoearning.com"))) as Response;
    expect(r.status).toBe(307);
    expect(r.headers.get("location")).toBe("http://app.algoearning.com/testing-dashboard");
  });

  it("shows the dashboard at the testing entry when signed in, keeping the address", async () => {
    await import("./proxy");
    const r = (await handler(signedIn, req("/testing-dashboard", "app.algoearning.com"))) as Response;
    expect(r.headers.get("x-middleware-rewrite")).toBe("http://app.algoearning.com/dashboard");
  });

  it("sends a signed-out visitor at the testing entry to sign-in and back", async () => {
    await import("./proxy");
    const r = (await handler(signedOut, req("/testing-dashboard", "app.algoearning.com"))) as Response;
    expect(r.status).toBe(307);
    expect(r.headers.get("location")).toBe(
      "http://app.algoearning.com/sign-in?redirect_url=%2Ftesting-dashboard",
    );
  });

  it("shows the product page at / when signed out, keeping the address", async () => {
    await import("./proxy");
    const r = (await handler(signedOut, req("/"))) as Response;
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
    expect(r.headers.get("location")).toBe("http://app.algoearning.com/");
  });

  it("sends a signed-in user from the sign-in and sign-up pages to the testing dashboard", async () => {
    await import("./proxy");
    for (const page of ["/sign-in", "/sign-up"]) {
      const r = (await handler(signedIn, req(page))) as Response;
      expect(r.headers.get("location")).toBe("http://localhost:3000/testing-dashboard");
    }
  });

  it("shows the product page at /test too", async () => {
    await import("./proxy");
    const r = (await handler(signedOut, req("/test"))) as Response;
    expect(r.headers.get("x-middleware-rewrite")).toBe("http://localhost:3000/landing.html");
  });

  it("sends the old /alpha-testing-dashboard link to the new one", async () => {
    await import("./proxy");
    const r = (await handler(signedIn, req("/alpha-testing-dashboard"))) as Response;
    expect(r.headers.get("location")).toBe("http://localhost:3000/testing-dashboard");
  });

  it("asks for the master password on every page when one is set", async () => {
    await import("./proxy");
    vi.stubEnv("APP_GATE_PASSWORD", "open-sesame");
    try {
      const ask = (await handler(signedIn, req("/runs/abc"))) as Response;
      expect(ask.status).toBe(401);
      expect(ask.headers.get("www-authenticate")).toContain("Basic");
      const withPassword = (pw: string) => {
        const r = req("/test");
        r.headers.set("authorization", `Basic ${btoa(`team:${pw}`)}`);
        return r;
      };
      expect(((await handler(signedOut, withPassword("wrong"))) as Response).status).toBe(401);
      const ok = (await handler(signedOut, withPassword("open-sesame"))) as Response;
      expect(ok.headers.get("x-middleware-rewrite")).toBe("http://localhost:3000/landing.html");
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
