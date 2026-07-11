import { Sparkles, Images, Handshake, Settings, MessageCircleHeart, Send } from "lucide-react";
import { cn } from "../lib/utils";
import { NineStar } from "./NineStar";
import { RosterAvatar } from "./ui";

export type Tab = "pipeline" | "products" | "x-posts" | "secretary" | "trust" | "settings";

const TABS: { id: Tab; label: string; icon: typeof Sparkles; avatar?: string }[] = [
  { id: "pipeline", label: "Pipeline", icon: Sparkles },
  { id: "products", label: "Products", icon: Images },
  { id: "x-posts", label: "Post to X", icon: Send },
  { id: "secretary", label: "Abigail", icon: MessageCircleHeart, avatar: "/abigail.jpg" },
  { id: "trust", label: "Trust", icon: Handshake },
  { id: "settings", label: "Settings", icon: Settings },
];

export function Nav({ tab, onChange }: { tab: Tab; onChange: (t: Tab) => void }) {
  return (
    <nav className="flex w-52 shrink-0 flex-col border-r border-slate-800 bg-slate-950/80">
      <div className="flex items-center gap-3 px-5 py-5">
        <NineStar className="text-amber-400" />
        <div>
          <div className="font-display text-base leading-tight text-slate-100">bahAI</div>
          <div className="text-[11px] uppercase tracking-[0.2em] text-slate-500">Workforce</div>
        </div>
      </div>
      <div className="mt-2 flex flex-col gap-1 px-3">
        {TABS.map(({ id, label, icon: Icon, avatar }) => (
          <button
            key={id}
            onClick={() => onChange(id)}
            className={cn(
              "flex items-center gap-3 rounded-lg px-3 py-2.5 text-sm font-medium transition-colors",
              tab === id
                ? "bg-amber-400/10 text-amber-300"
                : "text-slate-400 hover:bg-slate-800/60 hover:text-slate-200"
            )}
          >
            {avatar ? (
              <RosterAvatar src={avatar} name={label} className="h-5 w-5" />
            ) : (
              <Icon className="h-4 w-4" />
            )}
            {label}
          </button>
        ))}
      </div>
      <div className="mt-auto px-5 pb-5 text-[11px] leading-relaxed text-slate-600">
        Bookmarks 2″ × 6″, printed to order.
        <br />
        Every deliverable scored against the constitution.
      </div>
    </nav>
  );
}
