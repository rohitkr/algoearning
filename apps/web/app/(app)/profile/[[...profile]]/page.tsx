import type { Me } from "@algoearning/api-types";
import { Card, CardTitle, StatusPill } from "@algoearning/ui";
import { UserProfile } from "@clerk/nextjs";

import { apiGet } from "@/lib/api";
import { requireUser } from "@/lib/session";

export const metadata = { title: "Profile" };

const joined = new Intl.DateTimeFormat("en-IN", { dateStyle: "medium" });

/** Our account record (from the API) + Clerk's own security settings: password, Google connection, sessions. */
export default async function ProfilePage() {
  await requireUser();
  const me = await apiGet<Me>("/v1/me");
  return (
    <div className="mx-auto flex max-w-5xl flex-col gap-6">
      <h1 className="text-2xl font-semibold">Profile</h1>
      <Card className="flex flex-wrap items-center justify-between gap-4">
        {me.ok ? (
          <>
            <div>
              <CardTitle>AlgoEarning account</CardTitle>
              <p className="mt-1 text-lg font-semibold">{me.data.name ?? me.data.email}</p>
              <p className="text-sm text-muted">
                {me.data.email} · member since {joined.format(new Date(me.data.created_at))}
              </p>
            </div>
            <StatusPill tone="success">Active</StatusPill>
          </>
        ) : (
          <StatusPill tone="danger">Could not load your account: {me.error.message}</StatusPill>
        )}
      </Card>
      <UserProfile
        routing="path"
        path="/profile"
        appearance={{
          elements: { rootBox: "w-full", cardBox: "w-full max-w-none shadow-none border border-border" },
        }}
      />
    </div>
  );
}
