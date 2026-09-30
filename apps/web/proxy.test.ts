import { describe, expect, it, vi } from "vitest";

// the Clerk wrapper only forwards the callback: test the callback itself
let handler: (auth: unknown, req: unknown) => Response | undefined;
vi.mock("@clerk/nextjs/server", () => ({ clerkMiddleware: (fn: typeof handler) => (handler = fn) }));

const req = (url: string, host = "localhost:3000") => {
  const u = new URL(`http://${host}${url}`);
  return { nextUrl: Object.assign(u, { clone: () => new URL(u) }), headers: new Headers({ host }) };
};

describe("proxy", () => {
  it("forwards ICICI's ?apisession= redirect to Monitor's market-data page", async () => {
    await import("./proxy");
    const r = handler(null, req("/?apisession=57212559")) as Response;
    expect(r.status).toBe(307);
    expect(r.headers.get("location")).toBe("http://localhost:3000/monitor/market-data?apisession=57212559");
  });
  it("leaves the market-data page and other requests alone", async () => {
    await import("./proxy");
    expect(handler(null, req("/monitor/market-data?apisession=1"))).toBeUndefined();
    expect(handler(null, req("/dashboard"))).toBeUndefined();
  });
});
