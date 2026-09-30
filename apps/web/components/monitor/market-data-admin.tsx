"use client";

import type { MarketDataAdmin } from "@algoearning/api-types";
import { formatNumber } from "@algoearning/shared";
import { Button, Card, CardTitle, StatusPill, cn } from "@algoearning/ui";
import { ExternalLink } from "lucide-react";
import { useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";

import { inputClass } from "@/components/builder/fields";

import { Table, fmtDateTime, td } from "./bits";
import { useAdminAction } from "./use-admin-action";

function feedTone(d: MarketDataAdmin): { tone: "success" | "warning" | "danger"; text: string } {
  const beat = d.updated_at ? Date.now() - new Date(d.updated_at).getTime() : Infinity;
  if (!d.source || beat > 60_000) return { tone: "danger", text: "Feed not running" };
  if (d.source === "simulated") return { tone: "warning", text: "Simulated prices" };
  if (!d.connected)
    return { tone: "danger", text: d.session === "login needed" ? "Breeze login needed" : "Disconnected" };
  return { tone: "success", text: "Live from Breeze" };
}

/** Feed status, the daily Breeze login, and each index's last price. ICICI redirects the login back to this page
 * with ?apisession=...: the token is saved at once and removed from the address bar. */
export function MarketDataPanel({ data, redirectToken }: { data: MarketDataAdmin; redirectToken?: string }) {
  const { run, busy, note } = useAdminAction();
  const router = useRouter();
  const [token, setToken] = useState("");
  const sent = useRef(false);
  const s = feedTone(data);

  useEffect(() => {
    if (!redirectToken || sent.current) return;
    sent.current = true;
    void run(
      "session",
      "PUT",
      "/v1/admin/market-data/breeze-session",
      { session_token: redirectToken },
      "Breeze session saved: the feed reconnects within seconds.",
    ).then(() => router.replace("/monitor/market-data"));
  }, [redirectToken, run, router]);

  // the feed reports every few seconds: keep the page current
  useEffect(() => {
    const t = setInterval(() => router.refresh(), 5000);
    return () => clearInterval(t);
  }, [router]);

  return (
    <div className="flex flex-col gap-4">
      <div className="grid gap-4 lg:grid-cols-2">
        <Card className="flex flex-col gap-3">
          <div className="flex items-center justify-between gap-2">
            <CardTitle>Feed</CardTitle>
            <StatusPill tone={s.tone}>{s.text}</StatusPill>
          </div>
          <dl className="grid grid-cols-2 gap-x-4 gap-y-2 text-sm">
            <dt className="text-muted">Source</dt>
            <dd>{data.source ?? "–"}</dd>
            <dt className="text-muted">Instruments streamed</dt>
            <dd className="tabular-nums">
              {data.subscribed} of {data.wanted} wanted
            </dd>
            <dt className="text-muted">Breeze API calls today</dt>
            <dd className="tabular-nums">{data.api_calls_today} / 5,000</dd>
            <dt className="text-muted">Last price received</dt>
            <dd>{fmtDateTime(data.last_event)}</dd>
            <dt className="text-muted">Feed heartbeat</dt>
            <dd>{fmtDateTime(data.updated_at)}</dd>
          </dl>
          {data.error && <p className="text-sm text-loss">{data.error}</p>}
          {!data.source && (
            <p className="text-sm text-muted">
              Start it with <code>make dev</code> (or the <code>feed</code> service in Docker).
            </p>
          )}
        </Card>

        <Card className="flex flex-col gap-3">
          <div className="flex items-center justify-between gap-2">
            <CardTitle>Daily Breeze login</CardTitle>
            {data.session_expires_at && new Date(data.session_expires_at) > new Date() ? (
              <StatusPill tone="success">Valid until {fmtDateTime(data.session_expires_at)}</StatusPill>
            ) : (
              <StatusPill tone="warning">Needed today</StatusPill>
            )}
          </div>
          {data.login_url ? (
            <>
              <p className="text-sm text-muted">
                Log in with the platform&apos;s ICICI Direct account once each trading day. ICICI sends you
                back to this app and the session is saved automatically (the Breeze app&apos;s redirect URL is
                the app&apos;s address, e.g. http://localhost:3000). If it ever lands somewhere else, paste
                the <code>apisession</code> value below.
              </p>
              <Button asChild variant="secondary" className="w-fit">
                <a href={data.login_url} target="_blank" rel="noreferrer">
                  Log in to Breeze <ExternalLink className="size-4" aria-hidden />
                </a>
              </Button>
              <form
                className="flex flex-wrap gap-2"
                onSubmit={(e) => {
                  e.preventDefault();
                  void run(
                    "session",
                    "PUT",
                    "/v1/admin/market-data/breeze-session",
                    { session_token: token },
                    "Breeze session saved: the feed reconnects within seconds.",
                  ).then(() => setToken(""));
                }}
              >
                <input
                  aria-label="Breeze session token"
                  className={`${inputClass} max-w-xs flex-1`}
                  value={token}
                  onChange={(e) => setToken(e.target.value)}
                  placeholder="Session token"
                  autoComplete="off"
                  data-1p-ignore="true"
                  data-lpignore="true"
                />
                <Button type="submit" disabled={busy !== null || token.trim().length < 4}>
                  Save
                </Button>
              </form>
            </>
          ) : (
            <p className="text-sm text-muted">
              Set <code>BREEZE_API_KEY</code> and <code>BREEZE_API_SECRET</code> in <code>.env</code> to use
              Breeze. Until then the feed runs on simulated prices.
            </p>
          )}
          {note?.key === "session" && (
            <p role="status" className={cn("text-sm", note.ok ? "text-profit" : "text-loss")}>
              {note.text}
            </p>
          )}
        </Card>
      </div>

      <Card className="p-0">
        <div className="px-5 pt-4 pb-2">
          <CardTitle>Indices</CardTitle>
        </div>
        <Table head={["Index", "Last price", "At", "1-minute bars today"]}>
          {data.instruments.map((i) => (
            <tr key={i.code}>
              <td className={`${td} font-medium`}>{i.code}</td>
              <td className={`${td} tabular-nums`}>{i.ltp == null ? "–" : formatNumber(i.ltp)}</td>
              <td className={`${td} text-muted`}>{fmtDateTime(i.ts)}</td>
              <td className={`${td} tabular-nums`}>{i.bars_today}</td>
            </tr>
          ))}
        </Table>
      </Card>
    </div>
  );
}
