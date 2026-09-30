import { describe, expect, it } from "vitest";

import { NAV, isActive } from "./nav";

describe("nav", () => {
  it("has unique routes", () => {
    expect(new Set(NAV.map((n) => n.href)).size).toBe(NAV.length);
  });
  it("matches nested routes but not prefixes of other words", () => {
    expect(isActive("/strategies/12", "/strategies")).toBe(true);
    expect(isActive("/strategies", "/strategies")).toBe(true);
    expect(isActive("/strategiesx", "/strategies")).toBe(false);
  });
});
