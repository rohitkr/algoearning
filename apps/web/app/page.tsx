import { Button, Card, ThemeToggle } from "@algoearning/ui";
import { Blocks, PlugZap, Rocket } from "lucide-react";
import Link from "next/link";

import { AuthControls } from "@/components/auth-controls";
import { Logo } from "@/components/logo";

const STEPS = [
  {
    icon: PlugZap,
    title: "Connect your broker",
    body: "Zerodha today; more brokers soon. Your keys stay encrypted.",
  },
  {
    icon: Blocks,
    title: "Build or pick a strategy",
    body: "Multi-leg builder with live payoff, or start from a template.",
  },
  {
    icon: Rocket,
    title: "Deploy",
    body: "Paper trade first, then go live with stop-loss, targets and kill switches.",
  },
];

/** Landing page skeleton (the full page is phase 12). */
export default function Home() {
  return (
    <div className="flex min-h-dvh flex-col">
      <header className="mx-auto flex w-full max-w-6xl items-center justify-between px-4 py-4">
        <Logo />
        <div className="flex items-center gap-2">
          <ThemeToggle />
          <AuthControls />
        </div>
      </header>
      <main className="mx-auto flex w-full max-w-6xl flex-1 flex-col gap-16 px-4 py-16">
        <section className="flex max-w-2xl flex-col gap-5">
          <h1 className="text-4xl font-semibold tracking-tight md:text-5xl">
            Algo trading for Indian markets, on your own broker account.
          </h1>
          <p className="text-lg text-muted">
            Build options strategies, test them on live prices, and deploy them with the risk controls
            professional traders use.
          </p>
          <div className="flex gap-3">
            <Button size="lg" asChild>
              <Link href="/sign-up">Start free</Link>
            </Button>
            <Button size="lg" variant="secondary" asChild>
              <Link href="#how">How it works</Link>
            </Button>
          </div>
        </section>
        <section id="how" className="grid gap-4 md:grid-cols-3">
          {STEPS.map(({ icon: Icon, title, body }) => (
            <Card key={title}>
              <Icon className="size-5 text-primary-text" aria-hidden />
              <h2 className="mt-3 font-semibold">{title}</h2>
              <p className="mt-1 text-sm text-muted">{body}</p>
            </Card>
          ))}
        </section>
      </main>
      <footer className="border-t border-border py-6 text-center text-xs text-muted">
        Trading in derivatives involves risk. AlgoEarning provides software, not investment advice.
      </footer>
    </div>
  );
}
