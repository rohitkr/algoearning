import {
  BarChart3,
  CreditCard,
  FileText,
  FlaskConical,
  LayoutDashboard,
  Link2,
  type LucideIcon,
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
  { href: "/brokers", label: "Broker", icon: Link2, phase: 7 },
  { href: "/builder", label: "Strategy Builder", icon: Workflow, phase: 8 },
  { href: "/strategies", label: "Strategies", icon: BarChart3, phase: 8 },
  { href: "/backtesting", label: "Backtesting", icon: FlaskConical, phase: 16 },
  { href: "/reports", label: "Reports", icon: FileText, phase: 11 },
  { href: "/subscription", label: "Subscription", icon: CreditCard, phase: 6 },
];

export function isActive(pathname: string, href: string): boolean {
  return pathname === href || pathname.startsWith(`${href}/`);
}
