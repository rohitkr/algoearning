"use client";

import type { BrokerInfo } from "@algoearning/api-types";
import { Button } from "@algoearning/ui";
import { useAuth } from "@clerk/nextjs";
import { Copy, ExternalLink } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { type FormEvent, useEffect, useRef, useState } from "react";

import { ApiRequestError, apiPost } from "@/lib/client-api";

const input =
  "h-10 w-full rounded-lg border border-border bg-surface px-3 text-sm outline-none focus:border-primary focus:ring-1 focus:ring-primary";

/** "Add broker": the API key and secret go straight to our API, which encrypts them; they are never shown again. */
export function AddBrokerDialog({
  open,
  onClose,
  catalog,
}: {
  open: boolean;
  onClose: () => void;
  catalog: BrokerInfo[];
}) {
  const ref = useRef<HTMLDialogElement>(null);
  const { getToken } = useAuth();
  const router = useRouter();
  const available = catalog.filter((b) => b.available);
  const [broker, setBroker] = useState(available[0]?.code ?? "");
  const [error, setError] = useState<{ text: string; upgrade?: boolean } | null>(null);
  const [saving, setSaving] = useState(false);
  const [copied, setCopied] = useState(false);
  const info = catalog.find((b) => b.code === broker);

  useEffect(() => {
    const d = ref.current;
    if (!d) return;
    if (open && !d.open) d.showModal();
    if (!open && d.open) d.close();
  }, [open]);

  async function submit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const form = e.currentTarget; // React clears e.currentTarget once the handler awaits: keep the element
    const f = new FormData(form);
    setSaving(true);
    setError(null);
    try {
      await apiPost(
        "/v1/broker-accounts",
        {
          broker,
          client_id: String(f.get("client_id") ?? "").trim(),
          label: String(f.get("label") ?? "").trim() || null,
          api_key: String(f.get("api_key") ?? "").trim(),
          api_secret: String(f.get("api_secret") ?? "").trim(),
        },
        getToken,
      );
      form.reset();
      onClose();
      router.refresh();
    } catch (err) {
      const api = err instanceof ApiRequestError ? err : null;
      setError({ text: api?.message ?? (err as Error).message, upgrade: api?.body?.code === "plan_limit" });
    } finally {
      setSaving(false);
    }
  }

  return (
    <dialog
      ref={ref}
      onClose={onClose}
      className="m-auto w-[min(34rem,calc(100%-2rem))] rounded-2xl border border-border bg-surface p-0 text-foreground shadow-card backdrop:bg-black/50"
    >
      <form onSubmit={submit} className="flex flex-col gap-4 p-6" autoComplete="off">
        <h2 className="text-lg font-semibold">Add broker</h2>
        <label className="flex flex-col gap-1.5 text-sm">
          Broker
          <select className={input} value={broker} onChange={(e) => setBroker(e.target.value)}>
            {catalog.map((b) => (
              <option key={b.code} value={b.code} disabled={!b.available}>
                {b.name}
                {b.available ? "" : " (coming soon)"}
              </option>
            ))}
          </select>
        </label>
        {info?.redirect_url && (
          <div className="rounded-lg bg-surface-2 p-3 text-xs">
            <p className="text-muted">{info.notes}</p>
            <div className="mt-2 flex items-center gap-2">
              <code className="min-w-0 flex-1 truncate rounded bg-background px-2 py-1">
                {info.redirect_url}
              </code>
              <Button
                size="sm"
                variant="secondary"
                onClick={() => {
                  navigator.clipboard.writeText(info.redirect_url ?? "");
                  setCopied(true);
                }}
              >
                <Copy className="size-3.5" aria-hidden /> {copied ? "Copied" : "Copy"}
              </Button>
            </div>
            <a
              className="mt-2 inline-flex items-center gap-1 text-primary-text underline"
              href={info.developer_console}
              target="_blank"
              rel="noreferrer"
            >
              Open {info.name} developer console <ExternalLink className="size-3" aria-hidden />
            </a>
          </div>
        )}
        <div className="grid gap-4 sm:grid-cols-2">
          <label className="flex flex-col gap-1.5 text-sm">
            Client ID
            <input
              name="client_id"
              required
              minLength={2}
              maxLength={40}
              className={input}
              placeholder="AB1234"
            />
          </label>
          <label className="flex flex-col gap-1.5 text-sm">
            Label (optional)
            <input name="label" maxLength={80} className={input} placeholder="Main account" />
          </label>
        </div>
        <label className="flex flex-col gap-1.5 text-sm">
          API key
          <input name="api_key" required minLength={4} maxLength={200} className={input} />
        </label>
        <label className="flex flex-col gap-1.5 text-sm">
          API secret
          <input name="api_secret" type="password" required minLength={4} maxLength={200} className={input} />
          <span className="text-xs text-muted">
            Encrypted on our server. It is never shown again, even to you.
          </span>
        </label>
        {error && (
          <p role="alert" className="text-sm text-loss">
            {error.text}{" "}
            {error.upgrade && (
              <Link href="/subscription" className="underline">
                See plans
              </Link>
            )}
          </p>
        )}
        <div className="flex justify-end gap-2">
          <Button variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button type="submit" disabled={saving || !broker}>
            {saving ? "Saving…" : "Add broker"}
          </Button>
        </div>
      </form>
    </dialog>
  );
}
