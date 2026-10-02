/** Chart colours. The chart's frame (background, text, grid, candles) comes from the app's theme tokens so it
 * follows light and dark like every screen; SMC zones use green/cyan for bullish (demand) and red/orange for bearish
 * (supply), a shade per theme so labels stay readable on both backgrounds. */

/** The chart's axis shows Indian time: lightweight-charts draws times as UTC, so they are shifted by +05:30. */
export const IST_OFFSET_S = 19800;

type Side = { bull: string; bear: string };
type BoxColors = { fill: string; stroke: string; text: string };

export interface SmcPalette {
  fvg: { bull: BoxColors; bear: BoxColors };
  ob: { bull: BoxColors; bear: BoxColors };
  bos: Side;
  choch: Side;
  liquidity: Side;
}

const rgba = (rgb: string, a: number) => `rgba(${rgb}, ${a})`;

function box(rgb: string, text: string, fill: number, stroke: number): BoxColors {
  return { fill: rgba(rgb, fill), stroke: rgba(rgb, stroke), text };
}

const CYAN = { dark: "34, 211, 238", light: "8, 145, 178" };
const ORANGE = { dark: "251, 146, 60", light: "234, 88, 12" };
const GREEN = { dark: "52, 196, 106", light: "19, 122, 58" };
const RED = { dark: "242, 109, 109", light: "198, 40, 40" };

export const SMC_PALETTE: Record<"light" | "dark", SmcPalette> = {
  dark: {
    fvg: { bull: box(CYAN.dark, "#67e8f9", 0.13, 0.5), bear: box(ORANGE.dark, "#fdba74", 0.13, 0.5) },
    ob: { bull: box(GREEN.dark, "#86efac", 0.18, 0.7), bear: box(RED.dark, "#fca5a5", 0.18, 0.7) },
    bos: { bull: "#34c46a", bear: "#f26d6d" },
    choch: { bull: "#22d3ee", bear: "#fb923c" },
    liquidity: { bull: "#5eead4", bear: "#fdba74" },
  },
  light: {
    fvg: { bull: box(CYAN.light, "#0e7490", 0.12, 0.55), bear: box(ORANGE.light, "#9a3412", 0.12, 0.55) },
    ob: { bull: box(GREEN.light, "#166534", 0.14, 0.65), bear: box(RED.light, "#b91c1c", 0.14, 0.65) },
    bos: { bull: "#137a3a", bear: "#c62828" },
    choch: { bull: "#0e7490", bear: "#c2410c" },
    liquidity: { bull: "#0f766e", bear: "#9a3412" },
  },
};

export interface FrameColors {
  background: string;
  text: string;
  grid: string;
  border: string;
  up: string;
  down: string;
}

/** The theme tokens as they are now (call again after the theme changes). */
export function frameColors(): FrameColors {
  const css = getComputedStyle(document.documentElement);
  const v = (name: string, fallback: string) => css.getPropertyValue(name).trim() || fallback;
  return {
    background: v("--surface", "#16171c"),
    text: v("--muted", "#9aa3b4"),
    grid: v("--surface-2", "#1d1f26"),
    border: v("--border", "#2a2d36"),
    up: v("--profit", "#34c46a"),
    down: v("--loss", "#f26d6d"),
  };
}
