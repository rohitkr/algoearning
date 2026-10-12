import type { BacktestResultData, Trade } from "@/components/backtesting/result-view";

export type ReportMeta = {
  title: string; // the strategy's name
  underlying?: string;
  from?: string;
  to?: string;
  multiplier?: number;
  slippage?: number;
};

const IST = "Asia/Kolkata";
const parts = (iso: string) => {
  const f = new Intl.DateTimeFormat("en-GB", {
    timeZone: IST,
    weekday: "short",
    day: "2-digit",
    month: "short",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).formatToParts(new Date(iso));
  const g = (t: string) => f.find((p) => p.type === t)?.value ?? "";
  return {
    weekday: g("weekday"),
    date: `${g("day")} ${g("month")} ${g("year")}`,
    time: `${g("hour")}:${g("minute")}`,
  };
};
/** "15 Sep 2026 09:20" in Indian time. */
export const stamp = (iso: string) => {
  const p = parts(iso);
  return `${p.date} ${p.time}`;
};
const day = (iso: string) => {
  const p = parts(iso);
  return `${p.weekday}, ${p.date}`;
};
const money = (v: number | null | undefined) =>
  v == null ? "-" : v.toLocaleString("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const num = (v: unknown) => (typeof v === "number" ? v : null);
const pct = (v: unknown) => (typeof v === "number" ? `${(v * 100).toFixed(1)}%` : "-");

const cell = (v: string | number) => {
  const s = String(v);
  return /[",\n\r]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
};

/** One row per trade, for a spreadsheet: the day, the contract, both fills and the money. */
export function tradesCsv(trades: readonly Trade[]): string {
  const head = [
    "Day",
    "Entry date",
    "Symbol",
    "Side",
    "Qty",
    "Entry time",
    "Entry price",
    "Exit time",
    "Exit price",
    "Gross P&L",
    "Charges",
    "Net P&L",
    "Exit reason",
  ];
  const rows = trades.map((t) =>
    [
      parts(t.entry_time).weekday,
      parts(t.entry_time).date,
      t.contract,
      t.side,
      t.qty,
      stamp(t.entry_time),
      t.entry_price,
      stamp(t.exit_time),
      t.exit_price,
      t.gross,
      t.charges,
      t.net,
      t.reason,
    ]
      .map(cell)
      .join(","),
  );
  return `﻿${[head.map(cell).join(","), ...rows].join("\r\n")}\r\n`;
}

export const fileName = (meta: ReportMeta, ext: string) => {
  const slug =
    meta.title
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, "-")
      .replace(/^-|-$/g, "") || "backtest";
  return `${slug}${meta.from && meta.to ? `_${meta.from}_${meta.to}` : ""}.${ext}`;
};

function save(blob: Blob, name: string) {
  const url = URL.createObjectURL(blob);
  const a = Object.assign(document.createElement("a"), { href: url, download: name });
  a.click();
  URL.revokeObjectURL(url);
}

export function downloadCsv(res: BacktestResultData, meta: ReportMeta) {
  save(new Blob([tradesCsv(res.trades)], { type: "text/csv;charset=utf-8" }), fileName(meta, "csv"));
}

/** The summary figures as label/value pairs, in the order a reader wants them. */
export function summaryRows(res: BacktestResultData): [string, string][] {
  const s = res.summary;
  return [
    ["Net P&L", money(num(s.net_pnl))],
    ["Gross P&L", money(num(s.gross_pnl))],
    [
      "Charges",
      `${money(num(s.charges))}${num(s.charges_pct_of_gross) == null ? "" : ` (${s.charges_pct_of_gross}% of gross)`}`,
    ],
    ["Trades", `${s.trades ?? "-"} (${s.wins ?? 0} won, ${s.losses ?? 0} lost)`],
    ["Win rate", pct(s.win_rate)],
    ["Average win / loss", `${money(num(s.avg_win))} / ${money(num(s.avg_loss))}`],
    ["Profit factor", s.profit_factor == null ? "-" : String(s.profit_factor)],
    ["Expectancy per trade", money(num(s.expectancy))],
    ["Max drawdown", money(num(s.max_drawdown))],
    ["Max consecutive losses", String(s.max_consecutive_losses ?? "-")],
    ["Average holding time", num(s.avg_holding_minutes) == null ? "-" : `${s.avg_holding_minutes} min`],
    ["Trading days", `${s.trading_days ?? "-"} of ${s.days_replayed ?? "-"} replayed`],
  ];
}

export async function downloadPdf(res: BacktestResultData, meta: ReportMeta) {
  const { jsPDF } = await import("jspdf");
  const { default: autoTable } = await import("jspdf-autotable");
  const doc = new jsPDF({ orientation: "landscape", unit: "pt", format: "a4" });
  const W = doc.internal.pageSize.getWidth();
  const M = 36;
  const ink: [number, number, number] = [24, 28, 38];
  const muted: [number, number, number] = [110, 118, 132];
  const green: [number, number, number] = [18, 128, 74];
  const red: [number, number, number] = [196, 40, 52];
  const tone = (v: number) => (v >= 0 ? green : red);

  doc
    .setFont("helvetica", "bold")
    .setFontSize(18)
    .setTextColor(...ink);
  doc.text(`${meta.title} - backtest report`, M, 44);
  doc
    .setFont("helvetica", "normal")
    .setFontSize(10)
    .setTextColor(...muted);
  const generated = new Date().toLocaleString("en-IN", {
    timeZone: IST,
    dateStyle: "medium",
    timeStyle: "short",
  });
  const line = [
    meta.underlying,
    meta.from && meta.to ? `${meta.from} to ${meta.to}` : null,
    meta.multiplier != null ? `multiplier x${meta.multiplier}` : null,
    meta.slippage != null ? `slippage ${meta.slippage}%` : null,
  ]
    .filter(Boolean)
    .join("  |  ");
  doc.text(line, M, 62);
  doc.text(`Generated ${generated} IST`, M, 76);

  // headline + figures
  const net = num(res.summary.net_pnl) ?? 0;
  doc
    .setFont("helvetica", "bold")
    .setFontSize(26)
    .setTextColor(...tone(net));
  doc.text(`${net >= 0 ? "+" : ""}${money(net)}`, W - M, 52, { align: "right" });
  doc
    .setFont("helvetica", "normal")
    .setFontSize(9)
    .setTextColor(...muted);
  doc.text("Net P&L after charges (Rs)", W - M, 68, { align: "right" });

  const rows = summaryRows(res);
  const half = Math.ceil(rows.length / 2);
  const grid = Array.from({ length: half }, (_, i) => [
    rows[i]?.[0] ?? "",
    rows[i]?.[1] ?? "",
    rows[i + half]?.[0] ?? "",
    rows[i + half]?.[1] ?? "",
  ]);
  autoTable(doc, {
    startY: 92,
    body: grid,
    theme: "plain",
    styles: { fontSize: 9, cellPadding: 3, textColor: ink },
    columnStyles: {
      0: { textColor: muted, cellWidth: 150 },
      1: { fontStyle: "bold", cellWidth: 190 },
      2: { textColor: muted, cellWidth: 150 },
      3: { fontStyle: "bold" },
    },
    margin: { left: M, right: M },
  });
  let y = (doc as unknown as { lastAutoTable: { finalY: number } }).lastAutoTable.finalY + 10;

  if (res.warnings.length) {
    doc.setFontSize(9).setTextColor(...red);
    for (const w of res.warnings) {
      const lines = doc.splitTextToSize(`Note: ${w}`, W - 2 * M) as string[];
      doc.text(lines, M, y + 8);
      y += lines.length * 11 + 2;
    }
    y += 6;
  }

  // daily P&L
  if (res.daily.length) {
    doc
      .setFont("helvetica", "bold")
      .setFontSize(12)
      .setTextColor(...ink);
    doc.text("Daily P&L", M, y + 10);
    autoTable(doc, {
      startY: y + 16,
      head: [["Day", "Trades", "P&L", "Cumulative"]],
      body: res.daily.map((d) => [d.day, String(d.trades), money(d.pnl), money(d.cumulative)]),
      styles: { fontSize: 8.5, cellPadding: 3 },
      headStyles: { fillColor: ink, textColor: [255, 255, 255] },
      columnStyles: { 1: { halign: "right" }, 2: { halign: "right" }, 3: { halign: "right" } },
      didParseCell: (h) => {
        if (h.section === "head" && h.column.index > 0) h.cell.styles.halign = "right";
        if (h.section === "body" && (h.column.index === 2 || h.column.index === 3)) {
          h.cell.styles.textColor = tone(
            Number(res.daily[h.row.index]?.[h.column.index === 2 ? "pnl" : "cumulative"] ?? 0),
          );
        }
      },
      margin: { left: M, right: M },
      tableWidth: 420,
    });
    y = (doc as unknown as { lastAutoTable: { finalY: number } }).lastAutoTable.finalY;
  }

  // trades, a bold subtotal row per day
  doc.addPage();
  doc
    .setFont("helvetica", "bold")
    .setFontSize(12)
    .setTextColor(...ink);
  doc.text(
    `Trades${res.trades_total > res.trades.length ? ` (first ${res.trades.length} of ${res.trades_total})` : ""}`,
    M,
    44,
  );
  type Cell = string | number | { content: string; colSpan: number; styles: object };
  const byDay = new Map<string, Trade[]>();
  for (const t of res.trades) byDay.set(day(t.entry_time), [...(byDay.get(day(t.entry_time)) ?? []), t]);
  const body: Cell[][] = [];
  const shade = [238, 241, 246];
  for (const [label, list] of byDay) {
    const sub = list.reduce((a, t) => a + t.net, 0);
    body.push([
      { content: label, colSpan: 6, styles: { fontStyle: "bold", fillColor: shade } },
      {
        content: `${list.length} trade${list.length === 1 ? "" : "s"}   Net ${money(sub)}`,
        colSpan: 3,
        styles: { fontStyle: "bold", halign: "right", fillColor: shade, textColor: tone(sub) },
      },
    ]);
    for (const t of list) {
      const en = parts(t.entry_time);
      const ex = parts(t.exit_time);
      body.push([
        t.contract,
        t.side,
        t.qty,
        `${money(t.entry_price)}\n${en.time}`,
        `${money(t.exit_price)}\n${ex.date === en.date ? "" : `${ex.date} `}${ex.time}`,
        money(t.gross),
        money(t.charges),
        money(t.net),
        t.reason.replaceAll("₹", "Rs "),
      ]);
    }
  }
  autoTable(doc, {
    startY: 52,
    head: [["Symbol", "Side", "Qty", "Entry", "Exit", "Gross", "Charges", "Net P&L", "Exit reason"]],
    body,
    styles: { fontSize: 8, cellPadding: 3, textColor: ink, valign: "middle" },
    headStyles: { fillColor: ink, textColor: [255, 255, 255] },
    columnStyles: {
      0: { cellWidth: 150 },
      1: { cellWidth: 40 },
      2: { halign: "right", cellWidth: 45 },
      3: { halign: "right", cellWidth: 62 },
      4: { halign: "right", cellWidth: 90 },
      5: { halign: "right", cellWidth: 70 },
      6: { halign: "right", cellWidth: 60 },
      7: { halign: "right", fontStyle: "bold", cellWidth: 75 },
    },
    didParseCell: (h) => {
      if (h.section === "head" && [2, 3, 4, 5, 6, 7].includes(h.column.index)) h.cell.styles.halign = "right";
      if (h.section !== "body" || h.cell.colSpan > 1) return;
      const raw = String(h.cell.raw ?? "");
      if (h.column.index === 7) h.cell.styles.textColor = raw.startsWith("-") ? red : green;
      if (h.column.index === 1) h.cell.styles.textColor = raw === "BUY" ? green : red;
    },
    margin: { left: M, right: M },
  });

  const pages = doc.getNumberOfPages();
  for (let i = 1; i <= pages; i++) {
    doc.setPage(i);
    doc
      .setFont("helvetica", "normal")
      .setFontSize(8)
      .setTextColor(...muted);
    doc.text(
      "Simulated on stored history with the stated slippage and approximate charges. Past results do not predict future ones.",
      M,
      doc.internal.pageSize.getHeight() - 20,
    );
    doc.text(`Page ${i} of ${pages}`, W - M, doc.internal.pageSize.getHeight() - 20, { align: "right" });
  }
  save(doc.output("blob"), fileName(meta, "pdf"));
}
