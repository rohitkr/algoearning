import type { Run } from "@algoearning/api-types";
import { StatusPill } from "@algoearning/ui";

const dt = new Intl.DateTimeFormat("en-IN", { dateStyle: "medium", timeStyle: "short" });
export const fmt = (v: string | null | undefined) => (v ? dt.format(new Date(v)) : "–");

export function RunStatusPill({ run }: { run: Pick<Run, "status" | "engine_stale"> }) {
  if (run.engine_stale) return <StatusPill tone="warning">Engine not responding</StatusPill>;
  const map = {
    pending: { tone: "info", text: "Starting" },
    running: { tone: "success", text: "Running" },
    stopping: { tone: "warning", text: "Squaring off" },
    stopped: { tone: "neutral", text: "Stopped" },
    completed: { tone: "neutral", text: "Completed" },
    error: { tone: "danger", text: "Error" },
  } as const;
  const m = map[run.status];
  return <StatusPill tone={m.tone}>{m.text}</StatusPill>;
}

export function ModePill({ mode, dryRun = false }: { mode: Run["mode"]; dryRun?: boolean }) {
  if (mode === "live" && dryRun) return <StatusPill tone="warning">Live · dry run</StatusPill>;
  return mode === "live" ? (
    <StatusPill tone="danger">LIVE</StatusPill>
  ) : (
    <StatusPill tone="info">Paper</StatusPill>
  );
}
