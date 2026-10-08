"use client";

import type { SignalSetup, SignalSource, TelegramChat } from "@algoearning/api-types";
import { Button, Card, StatusPill, type StatusTone, cn, useConfirm } from "@algoearning/ui";
import { useAuth } from "@clerk/nextjs";
import { ExternalLink, Plus, Radio, Trash2, Unplug } from "lucide-react";
import { useRouter } from "next/navigation";
import { type FormEvent, type ReactNode, useState } from "react";

import { ApiRequestError, apiRequest } from "@/lib/client-api";

// Telegram's login code and 2-step password are typed here once and sent straight to our API, which passes them to
// Telegram and forgets them. They are not a login to this site: keep password managers from offering to save them.
const NOT_A_LOGIN = {
  autoComplete: "off",
  autoCorrect: "off",
  autoCapitalize: "off",
  spellCheck: false,
  "data-1p-ignore": "true",
  "data-lpignore": "true",
  "data-bwignore": "true",
} as const;

const input =
  "h-10 w-full rounded-lg border border-border bg-surface px-3 text-sm outline-none focus:border-primary focus:ring-1 focus:ring-primary";
const when = new Intl.DateTimeFormat("en-IN", {
  day: "numeric",
  month: "short",
  hour: "2-digit",
  minute: "2-digit",
  timeZone: "Asia/Kolkata",
});

const STATUS: Record<SignalSource["status"], { tone: StatusTone; text: string }> = {
  connected: { tone: "success", text: "Connected" },
  code_sent: { tone: "warning", text: "Waiting for the login code" },
  password_needed: { tone: "warning", text: "Waiting for the 2-step password" },
  flood_wait: { tone: "warning", text: "Telegram asks to wait" },
  needs_reconnect: { tone: "danger", text: "Reconnect Telegram" },
  disconnected: { tone: "neutral", text: "Disconnected" },
};

type Run = (key: string, fn: () => Promise<void>) => Promise<void>;

function Field({ label, hint, children }: { label: string; hint?: ReactNode; children: ReactNode }) {
  return (
    <div className="flex flex-col gap-1 text-sm">
      <label className="flex flex-col gap-1">
        <span className="font-medium">{label}</span>
        {children}
      </label>
      {hint && <span className="text-xs text-muted">{hint}</span>}
    </div>
  );
}

/** Phone (and optionally the user's own Telegram app): starts a login, or restarts one on an existing source. */
function LoginForm({
  setup,
  source,
  run,
  busy,
  onDone,
}: {
  setup: SignalSetup;
  source?: SignalSource;
  run: Run;
  busy: boolean;
  onDone?: () => void;
}) {
  const { getToken } = useAuth();
  const [ownApp, setOwnApp] = useState(!setup.platform_app || (source ? !source.platform_app : false));
  const needPhone = !source?.phone_masked;

  async function submit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const f = new FormData(e.currentTarget);
    const text = (k: string) => String(f.get(k) ?? "").trim() || null;
    const apiId = text("api_id");
    const body = {
      phone: text("phone"),
      ...(source ? {} : { label: text("label") }),
      ...(ownApp && (apiId || !source)
        ? { api_id: apiId ? Number(apiId) : null, api_hash: text("api_hash") }
        : {}),
    };
    await run(source?.id ?? "new", async () => {
      await apiRequest(
        "POST",
        source ? `/v1/signal-sources/${source.id}/login` : "/v1/signal-sources",
        body,
        getToken,
      );
      onDone?.();
    });
  }

  return (
    <form onSubmit={submit} className="flex flex-col gap-3">
      {!source && (
        <Field label="Name (optional)">
          <input name="label" className={input} maxLength={80} placeholder="e.g. VIP setups" />
        </Field>
      )}
      <Field
        label="Telegram phone number"
        hint={
          source?.phone_masked
            ? `Leave empty to use ${source.phone_masked}.`
            : "With the country code. Telegram sends a login code to your Telegram app."
        }
      >
        <input
          name="phone"
          type="tel"
          inputMode="tel"
          required={needPhone}
          className={input}
          placeholder="+91 98123 45678"
          {...NOT_A_LOGIN}
        />
      </Field>
      {setup.platform_app && (
        <label className="flex items-center gap-2 text-sm">
          <input type="checkbox" checked={ownApp} onChange={(e) => setOwnApp(e.target.checked)} />
          Use my own Telegram app (API ID and hash)
        </label>
      )}
      {ownApp && (
        <div className="grid gap-3 sm:grid-cols-2">
          <Field label="API ID">
            <input name="api_id" inputMode="numeric" className={input} required={!source} {...NOT_A_LOGIN} />
          </Field>
          <Field label="API hash">
            <input name="api_hash" className={cn(input, "font-mono")} required={!source} {...NOT_A_LOGIN} />
          </Field>
          <p className="text-xs text-muted sm:col-span-2">
            Create them at{" "}
            <a
              href="https://my.telegram.org/apps"
              target="_blank"
              rel="noreferrer"
              className="inline-flex items-center gap-0.5 text-primary underline"
            >
              my.telegram.org <ExternalLink className="size-3" aria-hidden />
            </a>{" "}
            (API development tools). They are encrypted and never shown again.
          </p>
        </div>
      )}
      <Button type="submit" className="self-start" disabled={busy}>
        Send login code
      </Button>
    </form>
  );
}

function CodeForm({ source, run, busy }: { source: SignalSource; run: Run; busy: boolean }) {
  const { getToken } = useAuth();
  const password = source.next_step === "password";
  async function submit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const form = e.currentTarget;
    const value = String(new FormData(form).get("secret") ?? "");
    await run(source.id, async () => {
      await apiRequest(
        "POST",
        `/v1/signal-sources/${source.id}/${password ? "password" : "code"}`,
        password ? { password: value } : { code: value },
        getToken,
      );
      form.reset();
    });
  }
  return (
    <form onSubmit={submit} className="flex flex-wrap items-end gap-2">
      <Field
        label={password ? "2-step verification password" : "Login code"}
        hint={password ? "Your Telegram cloud password. Used once, never stored." : source.status_detail}
      >
        <input
          name="secret"
          required
          className={cn(input, "w-56", password && "[-webkit-text-security:disc]")}
          inputMode={password ? "text" : "numeric"}
          {...NOT_A_LOGIN}
        />
      </Field>
      <Button type="submit" disabled={busy}>
        {password ? "Continue" : "Log in"}
      </Button>
    </form>
  );
}

function ChatPicker({ source, run, busy }: { source: SignalSource; run: Run; busy: boolean }) {
  const { getToken } = useAuth();
  const [chats, setChats] = useState<TelegramChat[] | null>(null);
  const [choice, setChoice] = useState("");
  const load = () =>
    run(source.id, async () => {
      setChats(
        await apiRequest<TelegramChat[]>("GET", `/v1/signal-sources/${source.id}/chats`, undefined, getToken),
      );
    });
  const save = () =>
    run(source.id, async () => {
      await apiRequest("PUT", `/v1/signal-sources/${source.id}/chat`, { chat_id: Number(choice) }, getToken);
      setChats(null);
    });
  if (!chats)
    return (
      <Button variant="secondary" className="self-start" disabled={busy} onClick={load}>
        {source.chat_id ? "Change chat" : "Choose the tips channel"}
      </Button>
    );
  return (
    <div className="flex flex-wrap items-end gap-2">
      <Field label="Channel or group to read">
        <select className={cn(input, "w-72")} value={choice} onChange={(e) => setChoice(e.target.value)}>
          <option value="">Choose…</option>
          {chats.map((c) => (
            <option key={c.id} value={c.id}>
              {c.title} {c.kind === "group" ? "(group)" : ""}
            </option>
          ))}
        </select>
      </Field>
      <Button disabled={busy || !choice} onClick={save}>
        Read this chat
      </Button>
      <Button variant="ghost" onClick={() => setChats(null)}>
        Cancel
      </Button>
    </div>
  );
}

function SourceCard({
  source,
  setup,
  run,
  busy,
}: {
  source: SignalSource;
  setup: SignalSetup;
  run: Run;
  busy: boolean;
}) {
  const { getToken } = useAuth();
  const confirm = useConfirm();
  const st = STATUS[source.status];
  const disconnect = async () => {
    const ok = await confirm({
      title: "Disconnect Telegram?",
      message:
        "AlgoEarning logs this session out at Telegram and deletes the phone number, API hash and session it stored. The chosen chat is kept, so you can reconnect later.",
      confirmLabel: "Disconnect",
      tone: "danger",
    });
    if (ok)
      await run(source.id, () =>
        apiRequest("POST", `/v1/signal-sources/${source.id}/disconnect`, undefined, getToken).then(
          () => undefined,
        ),
      );
  };
  const remove = async () => {
    const ok = await confirm({
      title: "Delete this signal source?",
      message: "Logs out at Telegram and removes the source.",
      confirmLabel: "Delete",
      tone: "danger",
    });
    if (ok)
      await run(source.id, () =>
        apiRequest("DELETE", `/v1/signal-sources/${source.id}`, undefined, getToken).then(() => undefined),
      );
  };
  return (
    <Card className="flex flex-col gap-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="flex items-center gap-3">
          <span className="grid size-10 place-items-center rounded-full bg-[#229ed9] text-white" aria-hidden>
            <Radio className="size-5" />
          </span>
          <div>
            <p className="font-semibold">{source.chat_title ?? source.label ?? "Telegram"}</p>
            <p className="text-xs text-muted">
              {[
                source.account_name,
                source.phone_masked,
                source.platform_app ? null : `own app ${source.api_id}`,
              ]
                .filter(Boolean)
                .join(" · ")}
            </p>
          </div>
        </div>
        <StatusPill tone={st.tone}>
          {st.text}
          {source.status === "flood_wait" && source.flood_until
            ? ` until ${when.format(new Date(source.flood_until))}`
            : ""}
        </StatusPill>
      </div>
      {source.status_detail && source.next_step !== "code" && (
        <p className="text-sm text-muted">{source.status_detail}</p>
      )}
      {(source.next_step === "code" || source.next_step === "password") && (
        <CodeForm source={source} run={run} busy={busy} />
      )}
      {source.status === "connected" && <ChatPicker source={source} run={run} busy={busy} />}
      {(source.next_step === "login" || source.status === "flood_wait") && (
        <LoginForm setup={setup} source={source} run={run} busy={busy} />
      )}
      {source.status === "connected" && source.chat_id && (
        <p className="text-sm text-muted">
          Reading <span className="font-medium text-foreground">{source.chat_title}</span> read-only: nothing
          is ever sent, forwarded or marked as read. Reading messages starts in the next release.
        </p>
      )}
      <div className="flex flex-wrap gap-2 border-t border-border pt-3">
        {source.status !== "disconnected" && (
          <Button size="sm" variant="secondary" disabled={busy} onClick={disconnect}>
            <Unplug className="size-4" aria-hidden /> Disconnect
          </Button>
        )}
        <Button size="sm" variant="ghost" disabled={busy} onClick={remove}>
          <Trash2 className="size-4" aria-hidden /> Delete
        </Button>
      </div>
    </Card>
  );
}

/** The user's Telegram signal sources: connect (phone -> code -> 2-step password -> chat), reconnect, disconnect. */
export function SignalSources({ sources, setup }: { sources: SignalSource[]; setup: SignalSetup }) {
  const router = useRouter();
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<{ key: string; text: string } | null>(null);
  const [adding, setAdding] = useState(sources.length === 0);
  const full = setup.max_sources !== null && setup.used >= setup.max_sources;

  const run: Run = async (key, fn) => {
    setBusy(key);
    setError(null);
    try {
      await fn();
      router.refresh();
    } catch (e) {
      setError({ key, text: e instanceof ApiRequestError ? e.message : (e as Error).message });
    } finally {
      setBusy(null);
    }
  };

  return (
    <div className="flex flex-col gap-4">
      <Card className="flex flex-col gap-2 border-warning/40 text-sm">
        <p className="font-medium">How this works</p>
        <ul className="list-disc space-y-1 pl-5 text-muted">
          <li>
            You log in with your own Telegram account, because a bot cannot read channels it does not run.
            Pick one channel or group you are a member of.
          </li>
          <li>
            Telegram has no read-only login: the session AlgoEarning keeps could act as you. It is encrypted,
            used only to read that one chat, and you can end it here (Disconnect) or in Telegram under
            Settings › Devices.
          </li>
        </ul>
      </Card>
      {sources.map((s) => (
        <div key={s.id} className="flex flex-col gap-2">
          <SourceCard source={s} setup={setup} run={run} busy={busy !== null} />
          {error?.key === s.id && (
            <p role="alert" className="text-sm text-loss">
              {error.text}
            </p>
          )}
        </div>
      ))}
      {adding ? (
        <Card className="flex flex-col gap-4">
          <h2 className="font-semibold">Connect Telegram</h2>
          <LoginForm setup={setup} run={run} busy={busy !== null} onDone={() => setAdding(false)} />
          {error?.key === "new" && (
            <p role="alert" className="text-sm text-loss">
              {error.text}
            </p>
          )}
        </Card>
      ) : (
        <Button
          variant="secondary"
          className="self-start"
          disabled={full}
          title={full ? `Your plan allows ${setup.max_sources} signal source(s)` : undefined}
          onClick={() => setAdding(true)}
        >
          <Plus className="size-4" aria-hidden /> Add a Telegram source
        </Button>
      )}
    </div>
  );
}
