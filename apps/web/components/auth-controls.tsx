import { Show, UserButton } from "@clerk/nextjs";
import { Button } from "@algoearning/ui";
import Link from "next/link";

/** Landing-page header actions: Sign in / Get started when signed out, Dashboard + account menu when signed in. */
export function AuthControls() {
  return (
    <>
      <Show when="signed-out">
        <Button variant="ghost" asChild>
          <Link href="/sign-in">Sign in</Link>
        </Button>
        <Button asChild>
          <Link href="/sign-up">Get started</Link>
        </Button>
      </Show>
      <Show when="signed-in">
        <Button variant="secondary" asChild>
          <Link href="/dashboard">Dashboard</Link>
        </Button>
        <UserButton />
      </Show>
    </>
  );
}
