import type { ServerIp } from "@algoearning/api-types";

const KITE_CONSOLE = "https://developers.kite.trade/apps";

/** Zerodha refuses orders from an IP the user's Kite app does not list. `always` shows the IP even when unchanged. */
export function ServerIpNotice({ ip, always = false }: { ip: ServerIp; always?: boolean }) {
  if (!ip.ip) return null;
  const changed = ip.changed_recently; // the API keeps this up for 3 days after a change
  if (!changed && !always) return null;
  const when = ip.changed_at
    ? new Date(ip.changed_at).toLocaleString("en-IN", {
        timeZone: "Asia/Kolkata",
        dateStyle: "medium",
        timeStyle: "short",
      })
    : null;
  return (
    <div
      role={changed ? "alert" : "note"}
      className={
        changed
          ? "rounded-lg border border-warning bg-surface p-3 text-sm"
          : "rounded-lg border border-border bg-surface p-3 text-sm"
      }
    >
      {changed ? (
        <p>
          <span className="font-semibold text-warning">Server IP changed</span> on {when} from{" "}
          <code>{ip.previous}</code> to <code className="font-semibold">{ip.ip}</code>. Zerodha refuses live
          orders until you replace the old IP in your Kite app on{" "}
          <a href={KITE_CONSOLE} target="_blank" rel="noreferrer" className="text-primary-text underline">
            developers.kite.trade
          </a>
          .
        </p>
      ) : (
        <p>
          Live orders reach Zerodha from <code className="font-semibold">{ip.ip}</code>. List this IP in your
          Kite app on{" "}
          <a href={KITE_CONSOLE} target="_blank" rel="noreferrer" className="text-primary-text underline">
            developers.kite.trade
          </a>
          .
        </p>
      )}
    </div>
  );
}
