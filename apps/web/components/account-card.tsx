import type { Me } from "@algoearning/api-types";
import { Card, CardTitle, StatusPill } from "@algoearning/ui";

import { apiGet } from "@/lib/api";

/** Proves the full chain: Clerk session -> token -> API verification -> our users table. */
export async function AccountCard() {
  const me = await apiGet<Me>("/v1/me");
  return (
    <Card>
      <CardTitle>Account</CardTitle>
      {me.ok ? (
        <>
          <p className="mt-2 font-semibold">{me.data.name ?? me.data.email}</p>
          <p className="text-sm text-muted">{me.data.email}</p>
          <div className="mt-3">
            <StatusPill tone="success">Signed in · API verified</StatusPill>
          </div>
        </>
      ) : (
        <div className="mt-3">
          <StatusPill tone="danger">API: {me.error.message}</StatusPill>
        </div>
      )}
    </Card>
  );
}
