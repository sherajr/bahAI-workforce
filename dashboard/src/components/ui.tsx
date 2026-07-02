import { forwardRef, type ButtonHTMLAttributes, type HTMLAttributes, type ReactNode } from "react";
import { Loader2 } from "lucide-react";
import { cn } from "../lib/utils";

// Minimal styled primitives (shadcn-style aesthetic, zero extra dependencies).

export const Card = ({ className, ...props }: HTMLAttributes<HTMLDivElement>) => (
  <div
    className={cn("rounded-xl border border-slate-800 bg-slate-900/70 shadow-sm", className)}
    {...props}
  />
);

export const CardHeader = ({ className, ...props }: HTMLAttributes<HTMLDivElement>) => (
  <div className={cn("px-5 pt-4 pb-2", className)} {...props} />
);

export const CardTitle = ({ className, ...props }: HTMLAttributes<HTMLHeadingElement>) => (
  <h3 className={cn("text-sm font-semibold tracking-wide text-slate-100", className)} {...props} />
);

export const CardContent = ({ className, ...props }: HTMLAttributes<HTMLDivElement>) => (
  <div className={cn("px-5 pb-5 pt-1", className)} {...props} />
);

interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: "primary" | "secondary" | "ghost" | "danger";
  loading?: boolean;
}

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(
  ({ className, variant = "primary", loading, disabled, children, ...props }, ref) => (
    <button
      ref={ref}
      disabled={disabled || loading}
      className={cn(
        "inline-flex items-center justify-center gap-2 rounded-lg px-4 py-2 text-sm font-medium transition-colors",
        "disabled:opacity-50 disabled:cursor-not-allowed",
        variant === "primary" &&
          "bg-amber-400 text-slate-950 hover:bg-amber-300 active:bg-amber-500",
        variant === "secondary" &&
          "border border-slate-700 bg-slate-800/60 text-slate-200 hover:bg-slate-800",
        variant === "ghost" && "text-slate-300 hover:bg-slate-800/60",
        variant === "danger" &&
          "border border-rose-500/40 bg-rose-500/10 text-rose-300 hover:bg-rose-500/20",
        className
      )}
      {...props}
    >
      {loading && <Loader2 className="h-4 w-4 animate-spin" />}
      {children}
    </button>
  )
);
Button.displayName = "Button";

export const BadgePill = ({ className, ...props }: HTMLAttributes<HTMLSpanElement>) => (
  <span
    className={cn(
      "inline-flex items-center rounded-full border px-2.5 py-0.5 text-xs font-semibold tracking-wide",
      className
    )}
    {...props}
  />
);

export const ProgressBar = ({
  value,
  colorClass = "bg-amber-400",
  className,
}: {
  value: number; // 0–100
  colorClass?: string;
  className?: string;
}) => (
  <div className={cn("h-2 w-full overflow-hidden rounded-full bg-slate-800", className)}>
    <div
      className={cn("h-full rounded-full transition-all duration-500", colorClass)}
      style={{ width: `${Math.min(100, Math.max(0, value))}%` }}
    />
  </div>
);

export const ErrorNote = ({ children }: { children: ReactNode }) => (
  <div className="rounded-lg border border-rose-500/40 bg-rose-500/10 px-4 py-3 text-sm text-rose-200">
    {children}
  </div>
);
