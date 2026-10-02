import { describe, expect, it } from "vitest";

import { sanitize } from "../components/charts/chart-board";
import { ALL_ON } from "../components/charts/controls";
import { LAYOUTS, MAX_CHARTS, placement, resizeTracks, template } from "../components/charts/layouts";
import { parseSse, upsertCandle } from "./chart-stream";

const c = (time: number, close = 1) => ({ time, open: 1, high: 2, low: 0, close, volume: 0 });

describe("parseSse", () => {
  it("returns complete events and keeps a partial one for the next chunk", () => {
    const { data, rest } = parseSse('data: {"a":1}\n\n: ping\n\ndata: {"b":\r\n');
    expect(data).toEqual(['{"a":1}']);
    expect(rest).toBe('data: {"b":\n');
    expect(parseSse(rest + "data: 2}\n\n").data).toEqual(['{"b":\n2}']);
  });
});

describe("upsertCandle", () => {
  it("appends, replaces the last, and inserts or replaces older candles", () => {
    let r = upsertCandle([], c(60));
    expect(r).toEqual({ list: [c(60)], atEnd: true });
    r = upsertCandle(r.list, c(120));
    r = upsertCandle(r.list, c(120, 5));
    expect(r.atEnd).toBe(true);
    expect(r.list.map((x) => x.close)).toEqual([1, 5]);
    r = upsertCandle(r.list, c(60, 9));
    expect(r.atEnd).toBe(false);
    expect(r.list.map((x) => [x.time, x.close])).toEqual([
      [60, 9],
      [120, 5],
    ]);
    r = upsertCandle([c(60), c(180)], c(120));
    expect(r.list.map((x) => x.time)).toEqual([60, 120, 180]);
  });
});

describe("sanitize the saved board", () => {
  const options = {
    instruments: [
      { code: "NIFTY", name: "Nifty 50" },
      { code: "SENSEX", name: "Sensex" },
    ],
    timeframes: [1, 5, 15],
  };
  it("keeps what is still possible and replaces the rest with defaults", () => {
    const b = sanitize(
      {
        layout: "4",
        charts: [
          { id: "a", key: "NIFTY", timeframe: 15, layers: { ...ALL_ON, fvg: false } },
          { id: "b", key: "FINNIFTY", timeframe: 5 },
          { id: "c", key: "SENSEX", timeframe: 7 },
        ],
        cols: [2, 1],
        rows: [1, -1],
      },
      options,
    );
    expect(b.layout).toBe("4");
    expect(b.charts).toHaveLength(4);
    expect(b.charts[0]).toEqual({ id: "a", key: "NIFTY", timeframe: 15, layers: { ...ALL_ON, fvg: false } });
    expect(b.charts[1]).toEqual({ id: "c1", key: "SENSEX", timeframe: 5, layers: ALL_ON });
    expect(b.charts[2]?.id).toBe("c2");
    // a board saved before overlays were remembered keeps its charts, with every overlay on
    const old = sanitize({ layout: "1", charts: [{ id: "x", key: "SENSEX", timeframe: 1 }] }, options);
    expect(old.charts[0]).toEqual({ id: "x", key: "SENSEX", timeframe: 1, layers: ALL_ON });
    expect(b.cols).toEqual([2, 1]);
    expect(b.rows).toEqual([1, 1]);
  });
  it("falls back to two charts side by side", () => {
    const b = sanitize("junk", options);
    expect(b.layout).toBe("2c");
    expect(b.charts.slice(0, 2).map((c) => c.key)).toEqual(["NIFTY", "SENSEX"]);
  });
});

describe("layouts", () => {
  it("resizes two neighbouring tracks, never below the minimum share", () => {
    expect(resizeTracks([1, 1], 0, 0.25)).toEqual([1.5, 0.5]);
    const [a, b] = resizeTracks([1, 1], 0, 0.9);
    expect(a).toBeCloseTo(1.6);
    expect(b).toBeCloseTo(0.4);
    expect(resizeTracks([1, 1, 1], 1, -0.1).reduce((s, x) => s + x, 0)).toBeCloseTo(3);
  });
  it("places cells on the lines between gutter tracks", () => {
    expect(placement({ col: 1, row: 0, rowSpan: 2 })).toEqual({ gridColumn: "3 / 4", gridRow: "1 / 4" });
    expect(template([2, 1], 8)).toBe("minmax(0, 2fr) 8px minmax(0, 1fr)");
    for (const l of LAYOUTS) expect(l.cells.length).toBeLessThanOrEqual(MAX_CHARTS);
  });
});
