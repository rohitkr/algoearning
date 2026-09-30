"use client";

import { Button } from "@algoearning/ui";
import { useAuth } from "@clerk/nextjs";
import { Download } from "lucide-react";
import { useState } from "react";

const BASE = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

/** Downloads the trade log as CSV (the API needs the session token, so a plain link would not do). */
export function CsvButton({ query }: { query: string }) {
  const { getToken } = useAuth();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  async function download() {
    setBusy(true);
    setError(null);
    try {
      const token = await getToken();
      const r = await fetch(`${BASE}/v1/reports/trades.csv?${query}`, {
        headers: token ? { Authorization: `Bearer ${token}` } : {},
      });
      if (!r.ok) throw new Error(`download failed (${r.status})`);
      const name = /filename="([^"]+)"/.exec(r.headers.get("content-disposition") ?? "")?.[1] ?? "trades.csv";
      const url = URL.createObjectURL(await r.blob());
      const a = Object.assign(document.createElement("a"), { href: url, download: name });
      a.click();
      URL.revokeObjectURL(url);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <span className="inline-flex items-center gap-2">
      <Button size="sm" variant="secondary" onClick={download} disabled={busy}>
        <Download className="size-4" aria-hidden /> {busy ? "Preparing…" : "CSV"}
      </Button>
      {error && <span className="text-xs text-loss">{error}</span>}
    </span>
  );
}
