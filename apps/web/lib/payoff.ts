/** Option payoff of a set of legs: at expiry (intrinsic value) and today (Black-Scholes with each leg's implied
 * volatility, solved from its current price). Used by the strategy pages' "legs it would trade now" preview. */

export type PayoffLeg = {
  action: "BUY" | "SELL";
  right: "CE" | "PE";
  strike: number;
  qty: number;
  price: number; // premium per unit now (the entry price)
  expiry: string; // yyyy-mm-dd
};

const RATE = 0.065; // risk-free rate (India, annual)
const YEAR_MS = 365 * 24 * 3600 * 1000;

const sign = (l: PayoffLeg) => (l.action === "BUY" ? 1 : -1);

function cdf(x: number): number {
  // Abramowitz-Stegun approximation of the standard normal CDF
  const t = 1 / (1 + 0.2316419 * Math.abs(x));
  const d = 0.3989423 * Math.exp((-x * x) / 2);
  const p = d * t * (0.3193815 + t * (-0.3565638 + t * (1.781478 + t * (-1.821256 + t * 1.330274))));
  return x > 0 ? 1 - p : p;
}

export function bs(right: "CE" | "PE", s: number, k: number, t: number, vol: number): number {
  if (t <= 0 || vol <= 0) return Math.max(0, right === "CE" ? s - k : k - s);
  const d1 = (Math.log(s / k) + (RATE + (vol * vol) / 2) * t) / (vol * Math.sqrt(t));
  const d2 = d1 - vol * Math.sqrt(t);
  return right === "CE"
    ? s * cdf(d1) - k * Math.exp(-RATE * t) * cdf(d2)
    : k * Math.exp(-RATE * t) * cdf(-d2) - s * cdf(-d1);
}

/** The volatility at which Black-Scholes gives `price` (bisection); a floor when the price is below intrinsic. */
export function impliedVol(right: "CE" | "PE", s: number, k: number, t: number, price: number): number {
  let lo = 0.01;
  let hi = 3;
  if (bs(right, s, k, t, lo) >= price) return lo;
  for (let i = 0; i < 60; i++) {
    const mid = (lo + hi) / 2;
    if (bs(right, s, k, t, mid) > price) hi = mid;
    else lo = mid;
  }
  return (lo + hi) / 2;
}

/** Years to the contract's close (15:30 IST on its expiry day) from `now`. */
export function yearsTo(expiry: string, now: Date): number {
  return Math.max(0, (Date.parse(`${expiry}T15:30:00+05:30`) - now.getTime()) / YEAR_MS);
}

export function atExpiry(legs: PayoffLeg[], s: number): number {
  return legs.reduce((sum, l) => {
    const intrinsic = Math.max(0, l.right === "CE" ? s - l.strike : l.strike - s);
    return sum + sign(l) * l.qty * (intrinsic - l.price);
  }, 0);
}

export type Analysis = {
  points: { s: number; expiry: number; today: number }[];
  maxProfit: number | null; // null: unlimited
  maxLoss: number | null; // null: unlimited (a negative number otherwise)
  breakevens: number[];
  premium: number; // received (positive) or paid (negative) at entry
  sd: number; // one standard deviation of the index to the nearest expiry, in points
};

/** Payoff curves over spot ± `span` (fraction), with the figures shown beside the chart. */
export function analyse(legs: PayoffLeg[], spot: number, now = new Date(), span = 0.06, n = 241): Analysis {
  const vols = legs.map((l) => impliedVol(l.right, spot, l.strike, yearsTo(l.expiry, now), l.price));
  const today = (s: number) =>
    legs.reduce(
      (sum, l, i) =>
        sum + sign(l) * l.qty * (bs(l.right, s, l.strike, yearsTo(l.expiry, now), vols[i]!) - l.price),
      0,
    );
  const lo = spot * (1 - span);
  const hi = spot * (1 + span);
  const points = Array.from({ length: n }, (_, i) => {
    const s = lo + ((hi - lo) * i) / (n - 1);
    return { s, expiry: atExpiry(legs, s), today: today(s) };
  });
  // the extremes over a wide range; a payoff still moving at its ends is unlimited that way
  const wide = Array.from({ length: 401 }, (_, i) => spot * (0.3 + (1.4 * i) / 400)).map((s) =>
    atExpiry(legs, s),
  );
  const slopeEnd = wide[400]! - wide[399]!;
  const slopeStart = wide[1]! - wide[0]!;
  const top = Math.max(...wide);
  const bottom = Math.min(...wide);
  const maxProfit = slopeEnd > 1e-6 || slopeStart < -1e-6 ? null : top;
  const maxLoss = slopeEnd < -1e-6 || slopeStart > 1e-6 ? null : bottom;
  const breakevens: number[] = [];
  for (let i = 1; i < points.length; i++) {
    const a = points[i - 1]!;
    const b = points[i]!;
    if ((a.expiry < 0 && b.expiry >= 0) || (a.expiry > 0 && b.expiry <= 0)) {
      breakevens.push(Math.round(a.s + ((b.s - a.s) * -a.expiry) / (b.expiry - a.expiry)));
    }
  }
  const nearest = legs.map((l) => l.expiry).sort()[0] ?? "";
  const atm = legs.reduce(
    (best, l, i) =>
      Math.abs(l.strike - spot) < Math.abs(best.k - spot) ? { k: l.strike, v: vols[i]! } : best,
    {
      k: Infinity,
      v: 0.15,
    },
  );
  return {
    points,
    maxProfit,
    maxLoss,
    breakevens,
    premium: legs.reduce((sum, l) => sum - sign(l) * l.qty * l.price, 0),
    sd: spot * atm.v * Math.sqrt(yearsTo(nearest, now)),
  };
}
