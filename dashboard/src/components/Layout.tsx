import type { ReactNode } from "react";
import { ActivityLog } from "./ActivityLog";
import { Nav, type Tab } from "./Nav";

export function Layout({
  tab,
  onTabChange,
  children,
}: {
  tab: Tab;
  onTabChange: (t: Tab) => void;
  children: ReactNode;
}) {
  return (
    <div className="flex h-full flex-col">
      <div className="flex min-h-0 flex-1">
        <Nav tab={tab} onChange={onTabChange} />
        <main className="min-w-0 flex-1 overflow-y-auto bg-slate-950 p-6">{children}</main>
      </div>
      <ActivityLog />
    </div>
  );
}
