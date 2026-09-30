import "server-only";

import { auth } from "@clerk/nextjs/server";

/** Call at the top of every signed-in page/layout: redirects to /sign-in (then back) when there is no session. */
export async function requireUser(): Promise<string> {
  const { userId } = await auth.protect();
  return userId;
}
