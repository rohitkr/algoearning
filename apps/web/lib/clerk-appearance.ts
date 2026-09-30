// Clerk's sign-in / user screens drawn with OUR theme tokens (CSS variables), so they switch between light and
// dark together with the rest of the app, with no second theme to keep in sync.
export const clerkAppearance = {
  variables: {
    colorPrimary: "var(--primary)",
    colorPrimaryForeground: "var(--primary-foreground)",
    colorBackground: "var(--surface)",
    colorForeground: "var(--foreground)",
    colorMutedForeground: "var(--muted)",
    colorMuted: "var(--surface-2)",
    colorInput: "var(--surface)",
    colorInputForeground: "var(--foreground)",
    colorBorder: "var(--border)",
    colorRing: "var(--ring)",
    colorDanger: "var(--loss)",
    colorSuccess: "var(--profit)",
    colorWarning: "var(--warning)",
    colorNeutral: "var(--foreground)",
    colorShadow: "rgb(0 0 0 / 0.25)",
    fontFamily: "var(--font-sans)",
    borderRadius: "0.6rem",
  },
  elements: {
    card: "shadow-card border border-border",
  },
} as const;
