import { SignIn } from "@clerk/nextjs";

import { AuthShell } from "@/components/auth-shell";
import { ClosedAlphaBanner } from "@/components/closed-alpha";

export const metadata = { title: "Sign in" };

export default function SignInPage() {
  return (
    <AuthShell>
      <div className="flex flex-col items-center gap-4">
        <ClosedAlphaBanner />
        {/* no "Sign up" link while sign-up is closed */}
        <SignIn appearance={{ elements: { footerAction: { display: "none" } } }} />
      </div>
    </AuthShell>
  );
}
