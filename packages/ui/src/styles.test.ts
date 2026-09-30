import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

const css = readFileSync(join(process.cwd(), "src/styles.css"), "utf8");

describe("global cursor rules", () => {
  it("point at everything clickable and forbid disabled controls", () => {
    for (const sel of [
      "button:not(:disabled)",
      "select:not(:disabled)",
      "a[href]",
      '[type="date"]',
      '[role="switch"]:not(:disabled)',
    ])
      expect(css).toContain(sel);
    expect(css).toMatch(/button:disabled[\s\S]*?cursor: not-allowed/);
    expect(css).toContain("::-webkit-calendar-picker-indicator");
  });
});
