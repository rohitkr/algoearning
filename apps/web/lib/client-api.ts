"use client";

import type { ApiError } from "@algoearning/api-types";

const BASE = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export class ApiRequestError extends Error {
  constructor(
    readonly status: number,
    readonly body: ApiError["error"] | null,
  ) {
    super(body?.message ?? `request failed (${status})`);
  }
}

type GetToken = () => Promise<string | null>;

/** Browser -> API call as the signed-in user (pass Clerk's getToken from useAuth()). */
export async function apiRequest<T>(
  method: string,
  path: string,
  body: unknown,
  getToken: GetToken,
): Promise<T> {
  const token = await getToken();
  const r = await fetch(`${BASE}${path}`, {
    method,
    headers: {
      ...(body === undefined ? {} : { "Content-Type": "application/json" }),
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (r.status === 204) return undefined as T;
  const json = (await r.json().catch(() => null)) as T | ApiError | null;
  if (!r.ok) throw new ApiRequestError(r.status, (json as ApiError | null)?.error ?? null);
  return json as T;
}

export const apiPost = <T>(path: string, body: unknown, getToken: GetToken) =>
  apiRequest<T>("POST", path, body, getToken);
