import { useEffect, useRef, useSyncExternalStore } from "react";
import { getActivityLog, subscribeActivity } from "../lib/api";
import { cn } from "../lib/utils";

// Bottom strip, always visible: the last 20 API calls the dashboard has made.
export function ActivityLog() {
  const entries = useSyncExternalStore(subscribeActivity, getActivityLog);
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const el = scrollRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [entries]);

  return (
    <div className="h-28 shrink-0 border-t border-slate-800 bg-slate-950">
      <div className="flex items-center justify-between px-4 py-1.5">
        <span className="text-[11px] font-semibold uppercase tracking-widest text-slate-500">
          Activity log
        </span>
        <span className="text-[11px] text-slate-600">last {entries.length} calls</span>
      </div>
      <div ref={scrollRef} className="h-[72px] overflow-y-auto px-4 pb-2 font-mono text-[11px] leading-5">
        {entries.length === 0 && <div className="text-slate-600">No API calls yet.</div>}
        {entries.map((e, i) => (
          <div key={i} className="flex gap-3 whitespace-nowrap">
            <span className="text-slate-600">[{e.ts}]</span>
            <span className="w-10 text-slate-400">{e.method}</span>
            <span className="flex-1 truncate text-slate-300">{e.path}</span>
            <span
              className={cn(
                e.status === "ERR" || (typeof e.status === "number" && e.status >= 400)
                  ? "text-rose-400"
                  : "text-emerald-400"
              )}
            >
              {e.status}
            </span>
            <span className="w-14 text-right text-slate-500">{e.ms}ms</span>
          </div>
        ))}
      </div>
    </div>
  );
}
