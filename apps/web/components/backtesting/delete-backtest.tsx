"use client";

import { Button } from "@algoearning/ui";
import { useAuth } from "@clerk/nextjs";
import { Trash2 } from "lucide-react";
import { useRouter } from "next/navigation";

import { apiRequest } from "@/lib/client-api";

export function DeleteBacktest({ id }: { id: string }) {
  const { getToken } = useAuth();
  const router = useRouter();
  return (
    <Button
      size="icon"
      variant="ghost"
      aria-label="Delete this backtest"
      onClick={async () => {
        if (!confirm("Delete this backtest?")) return;
        await apiRequest("DELETE", `/v1/backtests/${id}`, undefined, getToken);
        router.push("/backtesting");
      }}
    >
      <Trash2 className="size-4" aria-hidden />
    </Button>
  );
}
