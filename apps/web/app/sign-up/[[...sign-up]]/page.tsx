import { AuthShell } from "@/components/auth-shell";
import { ClosedAlphaBanner } from "@/components/closed-alpha";

export const metadata = { title: "Sign-up closed" };

// Sign-up is closed during the internal alpha: only existing accounts can sign in (the API refuses new ones too).
export default function SignUpPage() {
  return (
    <AuthShell>
      <div className="w-full max-w-sm">
        <ClosedAlphaBanner />
      </div>
    </AuthShell>
  );
}
