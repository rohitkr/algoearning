"use client";

import { Monitor, Moon, Sun } from "lucide-react";
import { useTheme } from "next-themes";
import { useEffect, useState } from "react";

import { Button } from "./button";

const ORDER = ["light", "dark", "system"] as const;
const LABEL = { light: "Light theme", dark: "Dark theme", system: "System theme" } as const;

/** Cycles light -> dark -> system. Renders a neutral placeholder until mounted, because the stored theme is
 * only known in the browser (avoids a hydration mismatch and a wrong icon flash). */
export function ThemeToggle() {
  const { theme, setTheme } = useTheme();
  const [mounted, setMounted] = useState(false);
  useEffect(() => setMounted(true), []);
  const current = (
    mounted && ORDER.includes(theme as (typeof ORDER)[number]) ? theme : "system"
  ) as (typeof ORDER)[number];
  const next = ORDER[(ORDER.indexOf(current) + 1) % ORDER.length] ?? "light";
  const Icon = current === "light" ? Sun : current === "dark" ? Moon : Monitor;
  return (
    <Button
      variant="ghost"
      size="icon"
      aria-label={`${LABEL[current]} (switch to ${next})`}
      onClick={() => setTheme(next)}
    >
      <Icon className="size-[18px]" aria-hidden />
    </Button>
  );
}
