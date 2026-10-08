import {
  BarChart3,
  CandlestickChart,
  CreditCard,
  FileText,
  FlaskConical,
  LayoutDashboard,
  PlayCircle,
  ShieldCheck,
  Link2,
  type LucideIcon,
  Radio,
  Workflow,
} from "lucide-react";

export interface NavItem {
  href: string;
  label: string;
  icon: LucideIcon;
  /** Phase that makes the page real; until then it is a placeholder. */
  phase: number;
}

export const NAV: NavItem[] = [
  { href: "/dashboard", label: "Dashboard", icon: LayoutDashboard, phase: 11 },
  { href: "/charts", label: "Charts", icon: CandlestickChart, phase: 10 },
  { href: "/brokers", label: "Broker", icon: Link2, phase: 7 },
  { href: "/signals", label: "Signals", icon: Radio, phase: 14 },
  { href: "/builder", label: "Strategy Builder", icon: Workflow, phase: 8 },
  { href: "/strategies", label: "Strategies", icon: BarChart3, phase: 8 },
  { href: "/runs", label: "Running", icon: PlayCircle, phase: 9 },
  { href: "/backtesting", label: "Backtesting", icon: FlaskConical, phase: 13 },
  { href: "/reports", label: "Reports", icon: FileText, phase: 11 },
  { href: "/subscription", label: "Subscription", icon: CreditCard, phase: 6 },
];

/** Shown to admins only (the API decides who is one). */
export const ADMIN_NAV: NavItem = { href: "/monitor", label: "Monitor", icon: ShieldCheck, phase: 8 };

export function isActive(pathname: string, href: string): boolean {
  return pathname === href || pathname.startsWith(`${href}/`);
}
