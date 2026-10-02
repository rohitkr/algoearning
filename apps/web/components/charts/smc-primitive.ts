import type { SmcBox, SmcLine, SmcOverlay } from "@algoearning/api-types";
import type {
  IChartApiBase,
  IPrimitivePaneRenderer,
  IPrimitivePaneView,
  ISeriesApi,
  ISeriesPrimitive,
  PrimitivePaneViewZOrder,
  SeriesAttachedParameter,
  SeriesType,
  Time,
  UTCTimestamp,
} from "lightweight-charts";

import { IST_OFFSET_S, type SmcPalette } from "./chart-theme";

type Target = Parameters<IPrimitivePaneRenderer["draw"]>[0];

export type SmcLayers = { fvg: boolean; ob: boolean; structure: boolean; liquidity: boolean };

/** Order blocks and FVGs as semi-transparent boxes stretched from their candle to the candle that mitigated them
 * (or to the live edge while open), BOS / CHoCH / liquidity as dashed lines with a label. Drawn under the candles.
 * The whole overlay is replaced on every update from the server, so a zone that a new bar changed is redrawn as
 * the server now sees it. */
export class SmcPrimitive implements ISeriesPrimitive<Time> {
  private chart: IChartApiBase<Time> | null = null;
  private series: ISeriesApi<SeriesType, Time> | null = null;
  private requestUpdate: (() => void) | null = null;
  private overlay: SmcOverlay | null = null;
  private layers: SmcLayers = { fvg: true, ob: true, structure: true, liquidity: true };
  private palette: SmcPalette;
  private readonly view: IPrimitivePaneView;

  constructor(palette: SmcPalette) {
    this.palette = palette;
    const renderer: IPrimitivePaneRenderer = { draw: (target) => this.draw(target) };
    this.view = { zOrder: (): PrimitivePaneViewZOrder => "bottom", renderer: () => renderer };
  }

  attached(param: SeriesAttachedParameter<Time>): void {
    this.chart = param.chart;
    this.series = param.series;
    this.requestUpdate = param.requestUpdate;
  }

  detached(): void {
    this.chart = this.series = this.requestUpdate = null;
  }

  paneViews(): readonly IPrimitivePaneView[] {
    return [this.view];
  }

  setOverlay(overlay: SmcOverlay | null): void {
    this.overlay = overlay;
    this.requestUpdate?.();
  }

  setLayers(layers: SmcLayers): void {
    this.layers = layers;
    this.requestUpdate?.();
  }

  setPalette(palette: SmcPalette): void {
    this.palette = palette;
    this.requestUpdate?.();
  }

  private x(t: number | null, width: number): number | null {
    if (t === null) return width; // still open: to the live edge
    const c = this.chart?.timeScale().timeToCoordinate((t + IST_OFFSET_S) as UTCTimestamp);
    return c ?? null;
  }

  private draw(target: Target): void {
    const overlay = this.overlay;
    const series = this.series;
    if (!overlay || !series || !this.chart) return;
    target.useMediaCoordinateSpace(({ context: ctx, mediaSize }) => {
      const width = mediaSize.width;
      ctx.save();
      ctx.font = "600 10px ui-sans-serif, system-ui, -apple-system, 'Segoe UI', sans-serif";
      ctx.textBaseline = "top";
      for (const b of overlay.boxes) {
        if ((b.kind === "fvg" && !this.layers.fvg) || (b.kind === "ob" && !this.layers.ob)) continue;
        this.drawBox(ctx, b, series, width);
      }
      for (const l of overlay.lines) {
        const on = l.kind === "liquidity" ? this.layers.liquidity : this.layers.structure;
        if (on) this.drawLine(ctx, l, series, width);
      }
      ctx.restore();
    });
  }

  private drawBox(
    ctx: CanvasRenderingContext2D,
    b: SmcBox,
    series: ISeriesApi<SeriesType, Time>,
    width: number,
  ) {
    const x1 = this.x(b.start, width);
    const x2 = this.x(b.end ?? null, width);
    const y1 = series.priceToCoordinate(b.top);
    const y2 = series.priceToCoordinate(b.bottom);
    if (x1 === null || x2 === null || y1 === null || y2 === null || x2 < 0 || x1 > width) return;
    const colors = this.palette[b.kind][b.side];
    const open = b.end === null;
    const h = Math.max(1, y2 - y1);
    ctx.globalAlpha = open ? 1 : 0.45;
    ctx.fillStyle = colors.fill;
    ctx.fillRect(x1, y1, x2 - x1, h);
    ctx.strokeStyle = colors.stroke;
    ctx.lineWidth = 1;
    ctx.setLineDash(b.kind === "fvg" ? [3, 3] : []);
    ctx.strokeRect(x1 + 0.5, y1 + 0.5, x2 - x1 - 1, h - 1);
    ctx.setLineDash([]);
    // the label sits at the box's right end: the live edge for an open zone, clear of the candles that made it
    const tw = ctx.measureText(b.label).width;
    if (h >= 11 && x2 - Math.max(x1, 0) >= tw + 8) {
      ctx.fillStyle = colors.text;
      ctx.fillText(b.label, Math.min(x2, width) - tw - 4, y1 + 2);
    }
    ctx.globalAlpha = 1;
  }

  private drawLine(
    ctx: CanvasRenderingContext2D,
    l: SmcLine,
    series: ISeriesApi<SeriesType, Time>,
    width: number,
  ) {
    const x1 = this.x(l.start, width);
    const x2 = this.x(l.end ?? null, width);
    const y = series.priceToCoordinate(l.price);
    if (x1 === null || x2 === null || y === null || x2 < 0 || x1 > width) return;
    const color = l.kind === "liquidity" ? this.palette.liquidity[l.side] : this.palette[l.kind][l.side];
    const yy = Math.round(y) + 0.5;
    ctx.strokeStyle = color;
    ctx.lineWidth = l.kind === "choch" ? 1.5 : 1;
    ctx.setLineDash(l.kind === "liquidity" ? [2, 3] : l.kind === "choch" ? [7, 4] : [5, 4]);
    ctx.beginPath();
    ctx.moveTo(x1, yy);
    ctx.lineTo(x2, yy);
    ctx.stroke();
    ctx.setLineDash([]);
    const text = l.label;
    const tw = ctx.measureText(text).width;
    const mid = Math.min(Math.max((x1 + x2) / 2, tw / 2 + 2), width - tw / 2 - 2);
    // a bullish break's label sits above its line, a bearish one's below, so they never cover the candles' side
    const ty = l.side === "bull" ? yy - 13 : yy + 3;
    ctx.fillStyle = color;
    ctx.fillText(text, mid - tw / 2, ty);
  }
}
