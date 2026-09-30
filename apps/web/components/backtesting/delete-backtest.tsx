"use client";

import { Button, useConfirm } from "@algoearning/ui";
import { useAuth } from "@clerk/nextjs";
import { Trash2 } from "lucide-react";
import { useRouter } from "next/navigation";

import { apiRequest } from "@/lib/client-api";

export function DeleteBacktest({ id }: { id: string }) {
  const { getToken } = useAuth();
  const router = useRouter();
  const confirm = useConfirm();
  return (
    <Button
      size="icon"
      variant="ghost"
      aria-label="Delete this backtest"
      onClick={async () => {
        const ok = await confirm({
          title: "Delete this backtest?",
          message: "Its result is removed. You can run it again any time.",
          confirmLabel: "Delete backtest",
          tone: "danger",
        });
        if (!ok) return;
        await apiRequest("DELETE", `/v1/backtests/${id}`, undefined, getToken);
        router.push("/backtesting");
      }}
    >
      <Trash2 className="size-4" aria-hidden />
    </Button>
  );
}
