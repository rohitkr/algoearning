import { describe, expect, it } from "vitest";

import { sanitize } from "../components/charts/chart-board";
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

describe("sanitize saved charts", () => {
  const options = {
    instruments: [
      { code: "NIFTY", name: "Nifty 50" },
      { code: "SENSEX", name: "Sensex" },
    ],
    timeframes: [1, 5, 15],
  };
  it("drops charts that are no longer possible and caps the count", () => {
    const saved = [
      { id: "a", key: "NIFTY", timeframe: 5 },
      { id: "b", key: "FINNIFTY", timeframe: 5 },
      { id: "c", key: "SENSEX", timeframe: 7 },
      { id: "d", key: "SENSEX", timeframe: 15 },
      { id: "e", key: "NIFTY", timeframe: 1 },
    ];
    expect(sanitize(saved, options)).toEqual([
      { id: "a", key: "NIFTY", timeframe: 5 },
      { id: "d", key: "SENSEX", timeframe: 15 },
    ]);
    expect(sanitize("junk", options)).toBeNull();
    expect(sanitize([], options)).toBeNull();
  });
});
