"use client";

import type { Connections } from "@algoearning/api-types";
import { useAuth } from "@clerk/nextjs";
import { TriangleAlert } from "lucide-react";
import Link from "next/link";
import { useEffect, useState } from "react";

import { apiRequest } from "@/lib/client-api";

const REFRESH_MS = 30_000;

type Notice = { key: string; text: string; href?: string; action?: string };

/** What is not connected yet, in words (pure, for the banner and its test). */
export function connectionNotices(c: Connections, isAdmin: boolean): Notice[] {
  const out: Notice[] = [];
  const md = c.market_data;
  const feed = isAdmin ? { href: "/monitor/market-data", action: "Open Market data" } : {};
  if (md.state === "login_needed") {
    out.push({
      key: "md",
      text: "Market data is not connected yet: the price feed is waiting for today's login. Entries and stop-losses wait until it connects.",
      ...(isAdmin ? { href: "/monitor/market-data", action: "Log in under Market data" } : {}),
    });
  } else if (md.state === "not_connected") {
    out.push({
      key: "md",
      text: `Market data is not connected${md.detail ? ` (${md.detail})` : ""}. It is reconnecting; entries and stop-losses wait until it connects.`,
      ...feed,
    });
  } else if (md.state === "no_prices") {
    out.push({
      key: "md",
      text: "Market data is connected but no prices have arrived for 2 minutes. Entries and stop-losses wait until prices come in.",
      ...feed,
    });
  }
  for (const b of c.brokers) {
    const name = `${b.broker_name} ${b.client_id}${b.label ? ` (${b.label})` : ""}`;
    out.push({
      key: b.id,
      text:
        b.status === "error"
          ? `${name} has a connection problem: strategies on this account cannot place orders.`
          : `${name} is not connected: today's login is pending. Strategies on this account cannot place orders until you log in.`,
      href: "/brokers",
      action: b.status === "error" ? "Check it" : "Log in",
    });
  }
  return out;
}

export function ConnectionNotices({ data, isAdmin }: { data: Connections; isAdmin: boolean }) {
  const notices = connectionNotices(data, isAdmin);
  if (notices.length === 0) return null;
  return (
    <div role="alert" className="flex flex-col gap-1 border-b border-warning/40 bg-warning/10 px-4 py-2">
      {notices.map((n) => (
        <p key={n.key} className="flex items-start gap-2 text-sm text-warning">
          <TriangleAlert className="mt-0.5 size-4 shrink-0" aria-hidden />
          <span>
            {n.text}
            {n.href && (
              <>
                {" "}
                <Link href={n.href} className="font-semibold underline underline-offset-2">
                  {n.action}
                </Link>
              </>
            )}
          </span>
        </p>
      ))}
    </div>
  );
}

/** A banner on every page while, on a trading day, the price feed or a broker account in use is not connected. */
export function ConnectionWarnings({ isAdmin }: { isAdmin: boolean }) {
  const { getToken } = useAuth();
  const [data, setData] = useState<Connections | null>(null);

  useEffect(() => {
    let alive = true;
    const load = async () => {
      if (document.visibilityState !== "visible") return;
      try {
        const c = await apiRequest<Connections>("GET", "/v1/connections", undefined, getToken);
        if (alive) setData(c);
      } catch {
        // an unreachable API is shown elsewhere
      }
    };
    void load();
    const t = setInterval(load, REFRESH_MS);
    document.addEventListener("visibilitychange", load);
    return () => {
      alive = false;
      clearInterval(t);
      document.removeEventListener("visibilitychange", load);
    };
  }, [getToken]);

  return data ? <ConnectionNotices data={data} isAdmin={isAdmin} /> : null;
}
