/** Chart layouts, like TradingView's: how many charts, and how they share the board. A layout is a grid of
 * `cols` x `rows` equal tracks (resizable afterwards) and one cell per chart, in chart order. */

export type LayoutId = "1" | "2c" | "2r" | "3c" | "3l" | "4";

export interface Cell {
  col: number; // first column track (0-based)
  row: number;
  colSpan?: number;
  rowSpan?: number;
}

export interface Layout {
  id: LayoutId;
  label: string;
  cols: number;
  rows: number;
  cells: Cell[];
}

export const LAYOUTS: Layout[] = [
  { id: "1", label: "1 chart", cols: 1, rows: 1, cells: [{ col: 0, row: 0 }] },
  {
    id: "2c",
    label: "2 charts side by side",
    cols: 2,
    rows: 1,
    cells: [
      { col: 0, row: 0 },
      { col: 1, row: 0 },
    ],
  },
  {
    id: "2r",
    label: "2 charts stacked",
    cols: 1,
    rows: 2,
    cells: [
      { col: 0, row: 0 },
      { col: 0, row: 1 },
    ],
  },
  {
    id: "3c",
    label: "3 charts side by side",
    cols: 3,
    rows: 1,
    cells: [
      { col: 0, row: 0 },
      { col: 1, row: 0 },
      { col: 2, row: 0 },
    ],
  },
  {
    id: "3l",
    label: "3 charts: one large, two stacked",
    cols: 2,
    rows: 2,
    cells: [
      { col: 0, row: 0, rowSpan: 2 },
      { col: 1, row: 0 },
      { col: 1, row: 1 },
    ],
  },
  {
    id: "4",
    label: "4 charts in a grid",
    cols: 2,
    rows: 2,
    cells: [
      { col: 0, row: 0 },
      { col: 1, row: 0 },
      { col: 0, row: 1 },
      { col: 1, row: 1 },
    ],
  },
];

/** The most charts any layout shows. */
export const MAX_CHARTS = Math.max(...LAYOUTS.map((l) => l.cells.length));

export const layoutById = (id: unknown): Layout | undefined => LAYOUTS.find((l) => l.id === id);

/** No track smaller than this share of the board. */
export const MIN_SHARE = 0.2;

/** Move the border between tracks `i` and `i + 1` by `delta` (a share of the whole board), keeping both tracks at
 * least MIN_SHARE and the total unchanged. */
export function resizeTracks(sizes: number[], i: number, delta: number): number[] {
  const a = sizes[i];
  const b = sizes[i + 1];
  if (a === undefined || b === undefined) return sizes;
  const total = sizes.reduce((s, x) => s + x, 0);
  const min = MIN_SHARE * total;
  const d = Math.max(min - a, Math.min(b - min, delta * total));
  const next = [...sizes];
  next[i] = a + d;
  next[i + 1] = b - d;
  return next;
}

export const equal = (n: number): number[] => Array.from({ length: n }, () => 1);

/** CSS grid tracks with a gutter track between neighbours: "2fr 8px 1fr". */
export function template(sizes: number[], gutter: number): string {
  return sizes.map((s) => `minmax(0, ${s}fr)`).join(` ${gutter}px `);
}

/** A cell's grid lines, counting the gutter tracks (track n sits on line 2n + 1). */
export function placement(c: Cell): { gridColumn: string; gridRow: string } {
  const col = 2 * c.col + 1;
  const row = 2 * c.row + 1;
  return {
    gridColumn: `${col} / ${col + 2 * (c.colSpan ?? 1) - 1}`,
    gridRow: `${row} / ${row + 2 * (c.rowSpan ?? 1) - 1}`,
  };
}
