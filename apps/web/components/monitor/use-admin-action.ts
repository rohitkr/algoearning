"use client";

import { useAuth } from "@clerk/nextjs";
import { useRouter } from "next/navigation";
import { useState } from "react";

import { ApiRequestError, apiRequest } from "@/lib/client-api";

/** Run an admin API call, refresh the server-rendered page, and keep the outcome for a status line. */
export function useAdminAction() {
  const { getToken } = useAuth();
  const router = useRouter();
  const [busy, setBusy] = useState<string | null>(null);
  const [note, setNote] = useState<{ key: string; ok: boolean; text: string } | null>(null);

  async function run<T>(
    key: string,
    method: string,
    path: string,
    body?: unknown,
    done?: string | ((r: T) => string),
  ) {
    setBusy(key);
    setNote(null);
    try {
      const r = await apiRequest<T>(method, path, body, getToken);
      router.refresh();
      if (done) setNote({ key, ok: true, text: typeof done === "string" ? done : done(r) });
      return r;
    } catch (e) {
      setNote({ key, ok: false, text: e instanceof ApiRequestError ? e.message : (e as Error).message });
      return undefined;
    } finally {
      setBusy(null);
    }
  }
  return { run, busy, note };
}
