import type { BrokerAccount, BrokerInfo } from "@algoearning/api-types";
import { StatusPill } from "@algoearning/ui";

import { BrokerAccounts } from "@/components/brokers/broker-accounts";
import { apiGet } from "@/lib/api";
import { requireUser } from "@/lib/session";

export const metadata = { title: "Broker" };

const ERRORS: Record<string, string> = {
  invalid_state: "That login link expired or was already used. Please click Login again.",
  wrong_account: "You logged in to a different broker account than the one registered here.",
  login_cancelled: "The broker login was cancelled.",
  session_expired: "The broker rejected the login (check the API key and secret).",
  rejected: "The broker rejected the login (check the API key and secret).",
  unreachable: "The broker could not be reached. Please try again.",
  unknown_account: "That broker account no longer exists.",
};

export default async function BrokersPage({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | undefined>>;
}) {
  await requireUser();
  const q = await searchParams;
  const [accounts, catalog] = await Promise.all([
    apiGet<BrokerAccount[]>("/v1/broker-accounts"),
    apiGet<BrokerInfo[]>("/v1/brokers"),
  ]);
  const banner = q.connected
    ? { tone: "success" as const, text: "Broker connected. Your session is valid until 06:00 tomorrow." }
    : q.error
      ? {
          tone: "danger" as const,
          text: `${ERRORS[q.error] ?? "The broker login failed."}${q.logged_in_as ? ` (logged in as ${q.logged_in_as})` : ""}`,
        }
      : null;
  return (
    <div className="mx-auto flex max-w-6xl flex-col gap-4">
      {banner && (
        <div role="status">
          <StatusPill tone={banner.tone}>{banner.text}</StatusPill>
        </div>
      )}
      {accounts.ok && catalog.ok ? (
        <BrokerAccounts accounts={accounts.data} catalog={catalog.data} />
      ) : (
        <StatusPill tone="danger">
          Could not load brokers:{" "}
          {(!accounts.ok ? accounts.error : !catalog.ok ? catalog.error : null)?.message}
        </StatusPill>
      )}
    </div>
  );
}
