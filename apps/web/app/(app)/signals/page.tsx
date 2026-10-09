import type { SignalSetup, SignalSource } from "@algoearning/api-types";
import { StatusPill } from "@algoearning/ui";

import { SignalFeed } from "@/components/signals/signal-feed";
import { TipReplayView } from "@/components/signals/tip-replay";
import { SignalSources } from "@/components/signals/signal-sources";
import { apiGet } from "@/lib/api";
import { requireUser } from "@/lib/session";

export const metadata = { title: "Signals" };

/** Signal sources (ADR 0025): the user's own Telegram login and the tips chat it reads. */
export default async function SignalsPage() {
  await requireUser();
  const [sources, setup] = await Promise.all([
    apiGet<SignalSource[]>("/v1/signal-sources"),
    apiGet<SignalSetup>("/v1/signal-sources/setup"),
  ]);
  return (
    <div className="mx-auto flex max-w-6xl flex-col gap-4">
      <div>
        <h1 className="text-xl font-semibold">Signals</h1>
        <p className="text-sm text-muted">
          Read a Telegram tips channel you follow. Signals come from a third party that AlgoEarning does not
          check or endorse; you decide what, if anything, to trade on them.
        </p>
      </div>
      {sources.ok && setup.ok ? (
        <>
          {sources.data
            .filter((s) => s.chat_id !== null)
            .map((s) => (
              <div key={s.id} className="flex flex-col gap-4">
                <SignalFeed source={s} />
                <TipReplayView source={s} />
              </div>
            ))}
          <SignalSources sources={sources.data} setup={setup.data} />
        </>
      ) : (
        <StatusPill tone="danger">
          Could not load signal sources:{" "}
          {(!sources.ok ? sources.error : !setup.ok ? setup.error : null)?.message}
        </StatusPill>
      )}
    </div>
  );
}
