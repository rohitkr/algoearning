"use client";

import type { FeedLogin, MarketDataAdmin } from "@algoearning/api-types";
import { formatNumber } from "@algoearning/shared";
import { Button, Card, CardTitle, StatusPill, cn } from "@algoearning/ui";
import { ExternalLink } from "lucide-react";
import { useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";

import { inputClass } from "@/components/builder/fields";

import { Table, fmtDateTime, td } from "./bits";
import { useAdminAction } from "./use-admin-action";

const PROVIDER: Record<string, string> = { breeze: "Breeze", kite: "Kite" };

function feedTone(d: MarketDataAdmin): { tone: "success" | "warning" | "danger"; text: string } {
  const beat = d.updated_at ? Date.now() - new Date(d.updated_at).getTime() : Infinity;
  if (!d.source || beat > 60_000) return { tone: "danger", text: "Feed not running" };
  if (d.source === "simulated") return { tone: "warning", text: "Simulated prices" };
  const name = PROVIDER[d.source] ?? d.source;
  if (!d.connected)
    return { tone: "danger", text: d.session === "login needed" ? `${name} login needed` : "Disconnected" };
  return { tone: "success", text: `Live from ${name}` };
}

/** How each provider's daily login works. Breeze sends the browser back with ?apisession=..., Kite with
 * ?request_token=...&status=success (a request token works for a few minutes only). */
const LOGIN = {
  breeze: {
    path: "/v1/admin/market-data/breeze-session",
    field: "session_token",
    placeholder: "Session token or the whole address",
    hint: (
      <>
        Log in with the platform&apos;s ICICI Direct account once each trading day. ICICI sends you back to
        this app and the session is saved automatically (the Breeze app&apos;s redirect URL is the app&apos;s
        address, e.g. http://localhost:3000). If it ever lands somewhere else, paste the address it landed on
        (or just its <code>apisession</code> value) below.
      </>
    ),
  },
  kite: {
    path: "/v1/admin/market-data/kite-session",
    field: "request_token",
    placeholder: "Request token or the whole address",
    hint: (
      <>
        Log in with the platform&apos;s own Zerodha account (not a user&apos;s) once each trading day; the
        session lasts until 06:00 the next morning. Set the platform Kite app&apos;s redirect URL to this
        page, e.g. https://app.algoearning.com/monitor/market-data, and the session is saved automatically. If
        it lands somewhere else, paste the <code>request_token</code> value below within a few minutes.
      </>
    ),
  },
} as const;

/** One provider's daily login: status, the login link, and a box to paste the token by hand. */
function LoginCard({
  login,
  inUse,
  run,
  busy,
  note,
}: {
  login: FeedLogin;
  inUse: boolean;
  run: ReturnType<typeof useAdminAction>["run"];
  busy: string | null;
  note: ReturnType<typeof useAdminAction>["note"];
}) {
  const [token, setToken] = useState("");
  const how = LOGIN[login.provider];
  const name = PROVIDER[login.provider];
  const key = `session-${login.provider}`;
  return (
    <Card className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <CardTitle>
          Daily {name} login
          {!inUse && (
            <span className="ml-2 text-xs font-normal text-muted">not the feed&apos;s source now</span>
          )}
        </CardTitle>
        {login.session_expires_at && new Date(login.session_expires_at) > new Date() ? (
          <StatusPill tone="success">Valid until {fmtDateTime(login.session_expires_at)}</StatusPill>
        ) : (
          <StatusPill tone={inUse ? "warning" : "neutral"}>Needed today</StatusPill>
        )}
      </div>
      <p className="text-sm text-muted">{how.hint}</p>
      {login.provider === "kite" && (
        <p className="text-sm">
          {login.account && login.session_expires_at && new Date(login.session_expires_at) > new Date() ? (
            <>
              Logged in as <span className="font-medium">{login.account}</span>.{" "}
            </>
          ) : null}
          <span className="text-muted">
            {login.expected_account ? (
              <>
                Only Zerodha account{" "}
                <span className="font-medium text-foreground">{login.expected_account}</span> is accepted.
              </>
            ) : (
              <>
                Any Zerodha account is accepted: set <code>KITE_FEED_CLIENT_ID</code> to allow only the
                platform&apos;s.
              </>
            )}
          </span>
        </p>
      )}
      <Button asChild variant="secondary" className="w-fit">
        <a href={login.login_url} target="_blank" rel="noreferrer">
          Log in to {name} <ExternalLink className="size-4" aria-hidden />
        </a>
      </Button>
      <form
        className="flex flex-wrap gap-2"
        onSubmit={(e) => {
          e.preventDefault();
          void run(
            key,
            "PUT",
            how.path,
            { [how.field]: token },
            `${name} session saved: the feed reconnects within seconds.`,
          ).then(() => setToken(""));
        }}
      >
        <input
          aria-label={`${name} ${how.placeholder.toLowerCase()}`}
          className={`${inputClass} max-w-xs flex-1`}
          value={token}
          onChange={(e) => setToken(e.target.value)}
          placeholder={how.placeholder}
          autoComplete="off"
          data-1p-ignore="true"
          data-lpignore="true"
        />
        <Button type="submit" disabled={busy !== null || token.trim().length < 4}>
          Save
        </Button>
      </form>
      {note?.key === key && (
        <p role="status" className={cn("text-sm", note.ok ? "text-profit" : "text-loss")}>
          {note.text}
        </p>
      )}
    </Card>
  );
}

/** Feed status, the daily provider login (Breeze, or the platform Kite account), and each index's last price.
 * The provider's login redirects back to this page with its token, which is saved at once and removed from the
 * address bar. */
export function MarketDataPanel({
  data,
  redirect,
}: {
  data: MarketDataAdmin;
  redirect?: { provider: "breeze" | "kite"; token: string };
}) {
  const { run, busy, note } = useAdminAction();
  const router = useRouter();
  const sent = useRef(false);
  const s = feedTone(data);

  useEffect(() => {
    if (!redirect || sent.current) return;
    sent.current = true;
    const how = LOGIN[redirect.provider];
    void run(
      `session-${redirect.provider}`,
      "PUT",
      how.path,
      { [how.field]: redirect.token },
      `${PROVIDER[redirect.provider]} session saved: the feed reconnects within seconds.`,
    ).then(() => router.replace("/monitor/market-data"));
  }, [redirect, run, router]);

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
            {data.source === "breeze" && (
              <>
                <dt className="text-muted">Breeze API calls today</dt>
                <dd className="tabular-nums">{data.api_calls_today} / 5,000</dd>
              </>
            )}
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

        {data.logins.map((l) => (
          <LoginCard
            key={l.provider}
            login={l}
            inUse={data.source === l.provider || (!data.source && data.logins.length === 1)}
            run={run}
            busy={busy}
            note={note}
          />
        ))}
        {data.logins.length === 0 && (
          <Card className="flex flex-col gap-3">
            <CardTitle>Daily login</CardTitle>
            <p className="text-sm text-muted">
              Set <code>BREEZE_API_KEY</code> and <code>BREEZE_API_SECRET</code> (or the platform Kite
              app&apos;s <code>KITE_FEED_API_KEY</code> and <code>KITE_FEED_API_SECRET</code>) in{" "}
              <code>.env</code> for live prices. Until then the feed runs on simulated prices.
            </p>
          </Card>
        )}
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
