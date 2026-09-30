"use client";

import type { NotificationSettings, TelegramLink } from "@algoearning/api-types";
import { Button, Card, CardTitle, StatusPill, Switch, cn } from "@algoearning/ui";
import { useAuth } from "@clerk/nextjs";
import { useRouter } from "next/navigation";
import { useState } from "react";

import { inputClass } from "@/components/builder/fields";
import { ApiRequestError, apiRequest } from "@/lib/client-api";

export function NotificationSettingsForm({ initial }: { initial: NotificationSettings }) {
  const { getToken } = useAuth();
  const router = useRouter();
  const [s, setS] = useState(initial);
  const [email, setEmail] = useState(initial.email_address ?? "");
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<{ ok: boolean; text: string } | null>(null);
  const [link, setLink] = useState<TelegramLink | null>(null);

  async function call<T>(
    method: string,
    path: string,
    body?: unknown,
    done?: string,
  ): Promise<T | undefined> {
    setBusy(true);
    setNote(null);
    try {
      const r = await apiRequest<T>(method, path, body, getToken);
      if (done) setNote({ ok: true, text: done });
      router.refresh();
      return r;
    } catch (e) {
      setNote({ ok: false, text: e instanceof ApiRequestError ? e.message : (e as Error).message });
      return undefined;
    } finally {
      setBusy(false);
    }
  }

  const toggle = (key: string, on: boolean) =>
    setS((o) => ({
      ...o,
      events: on ? [...new Set([...o.events, key])] : o.events.filter((k) => k !== key),
    }));

  async function save() {
    const r = await call<NotificationSettings>(
      "PUT",
      "/v1/me/notifications",
      {
        email_enabled: s.email_enabled,
        email_address: email.trim() || null,
        telegram_enabled: s.telegram_enabled,
        events: s.events,
      },
      "Saved.",
    );
    if (r) setS(r);
  }

  return (
    <div className="flex flex-col gap-4">
      <Card className="flex flex-col gap-4">
        <CardTitle>Where to send alerts</CardTitle>
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="min-w-56 flex-1">
            <p className="font-medium">Email</p>
            <p className="text-sm text-muted">
              {s.email_available
                ? "Sent to the address below."
                : "Not set up on this server yet (SMTP_HOST in .env)."}
            </p>
            <input
              aria-label="Email address"
              type="email"
              className={`${inputClass} mt-2 max-w-sm`}
              placeholder={s.account_email}
              value={email}
              onChange={(e) => setEmail(e.target.value)}
            />
          </div>
          <Switch
            checked={s.email_enabled}
            onCheckedChange={(on) => setS({ ...s, email_enabled: on })}
            aria-label="Send email alerts"
          />
        </div>
        <div className="flex flex-wrap items-start justify-between gap-3 border-t border-border pt-4">
          <div className="min-w-56 flex-1">
            <p className="flex items-center gap-2 font-medium">
              Telegram {s.telegram_connected && <StatusPill tone="success">Connected</StatusPill>}
            </p>
            {!s.telegram_available ? (
              <p className="text-sm text-muted">
                Not set up on this server yet (TELEGRAM_BOT_TOKEN in .env).
              </p>
            ) : s.telegram_connected ? (
              <button
                type="button"
                className="text-sm text-primary-text underline"
                onClick={async () => {
                  const r = await call<NotificationSettings>(
                    "DELETE",
                    "/v1/me/notifications/telegram",
                    undefined,
                    "Disconnected.",
                  );
                  if (r) setS(r);
                }}
              >
                Disconnect
              </button>
            ) : (
              <div className="mt-1 flex flex-col items-start gap-2 text-sm text-muted">
                <span>Press Connect, open the link in Telegram and tap Start. Then come back here.</span>
                <Button
                  size="sm"
                  variant="secondary"
                  disabled={busy}
                  onClick={async () =>
                    setLink((await call<TelegramLink>("POST", "/v1/me/notifications/telegram/link")) ?? null)
                  }
                >
                  Connect Telegram
                </Button>
                {link &&
                  (link.url ? (
                    <a
                      href={link.url}
                      target="_blank"
                      rel="noreferrer"
                      className="text-primary-text underline"
                    >
                      Open Telegram
                    </a>
                  ) : (
                    <span>
                      Send <code>/start {link.code}</code> to your bot.
                    </span>
                  ))}
              </div>
            )}
          </div>
          <Switch
            checked={s.telegram_enabled}
            disabled={!s.telegram_connected}
            onCheckedChange={(on) => setS({ ...s, telegram_enabled: on })}
            aria-label="Send Telegram alerts"
          />
        </div>
      </Card>

      <Card className="flex flex-col gap-3">
        <CardTitle>What to tell you about</CardTitle>
        <ul className="flex flex-col divide-y divide-border">
          {s.catalog.map((e) => (
            <li key={e.key} className="flex items-center justify-between gap-3 py-2.5">
              <span>
                <span className="text-sm font-medium">{e.label}</span>
                <span className="block text-xs text-muted">{e.hint}</span>
              </span>
              <Switch
                checked={s.events.includes(e.key)}
                onCheckedChange={(on) => toggle(e.key, on)}
                aria-label={e.label}
              />
            </li>
          ))}
        </ul>
        <div className="flex flex-wrap items-center gap-3">
          <Button onClick={save} disabled={busy}>
            Save
          </Button>
          <Button
            variant="secondary"
            disabled={busy}
            onClick={() =>
              call("POST", "/v1/me/notifications/test", undefined, "Test queued: it arrives within seconds.")
            }
          >
            Send a test
          </Button>
          {note && (
            <p role="status" className={cn("text-sm", note.ok ? "text-profit" : "text-loss")}>
              {note.text}
            </p>
          )}
        </div>
      </Card>
    </div>
  );
}
