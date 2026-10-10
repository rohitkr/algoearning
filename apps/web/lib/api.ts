import "server-only";

import { auth } from "@clerk/nextjs/server";
import type { ApiError, Health } from "@algoearning/api-types";

import { API_URL } from "./env";

/** The API's /health, or null when it cannot be reached (never throws: the page still renders). */
export async function fetchHealth(signal?: AbortSignal): Promise<Health | null> {
  try {
    const r = await fetch(`${API_URL}/health`, {
      cache: "no-store",
      signal: signal ?? AbortSignal.timeout(2000),
    });
    return r.ok ? ((await r.json()) as Health) : null;
  } catch {
    return null;
  }
}

export type ApiResult<T> = { ok: true; data: T } | { ok: false; status: number; error: ApiError["error"] };

/** Server-side call to the API as the signed-in user: Clerk session token as a Bearer token. The API verifies
 * it independently, so this server never vouches for anyone. */
export async function apiGet<T>(path: string, timeoutMs = 5000): Promise<ApiResult<T>> {
  const { getToken } = await auth();
  const token = await getToken();
  try {
    const r = await fetch(`${API_URL}${path}`, {
      cache: "no-store",
      headers: token ? { Authorization: `Bearer ${token}` } : {},
      signal: AbortSignal.timeout(timeoutMs),
    });
    if (r.ok) return { ok: true, data: (await r.json()) as T };
    const body = (await r.json().catch(() => null)) as ApiError | null;
    return {
      ok: false,
      status: r.status,
      error: body?.error ?? { code: "http_error", message: r.statusText, details: null, request_id: null },
    };
  } catch (e) {
    return {
      ok: false,
      status: 0,
      error: { code: "unreachable", message: (e as Error).message, details: null, request_id: null },
    };
  }
}
