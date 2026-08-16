import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { ShieldCheck } from "lucide-react";
import { api } from "../../lib/api";
import { agentLabel, formatDate } from "../../lib/utils";
import { Button, Card, CardContent, CardHeader, CardTitle, ErrorNote } from "../ui";

/**
 * Anything an agent asked to do that costs money or changes a saved product.
 *
 * Nothing here has run. The gate lives in the tool handler on the backend
 * (colony_tools), not in the agent's prompt, so an agent cannot talk its way
 * past this list — approving here is the only path that executes any of it.
 */
export function ActionQueue({ onResolved }: { onResolved: () => void }) {
  const [busy, setBusy] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [outcome, setOutcome] = useState<string | null>(null);
  const actions = useQuery({
    queryKey: ["colony-actions"],
    queryFn: () => api.getColonyActions("pending"),
    refetchInterval: 15_000,
  });

  const resolve = async (id: number, approve: boolean) => {
    setBusy(id);
    setError(null);
    setOutcome(null);
    try {
      const res = await api.resolveColonyAction(id, approve);
      setOutcome(res.result === "done"
        ? (res.outcome ?? "Done.")
        : "Declined — nothing ran.");
      actions.refetch();
      onResolved();
    } catch (e) {
      setError(e instanceof Error ? e.message : "That action failed.");
      actions.refetch();
    } finally {
      setBusy(null);
    }
  };

  const pending = actions.data?.actions ?? [];

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <ShieldCheck className="h-4 w-4 text-amber-300" />
          Waiting for you
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        <p className="text-sm text-slate-400">
          When an agent wants to spend money or change a saved product, it stops here
          instead. Nothing on this list has happened yet.
        </p>
        {error && <ErrorNote>{error}</ErrorNote>}
        {outcome && (
          <p className="rounded-lg border border-emerald-400/30 bg-emerald-400/5 px-3 py-2
                        text-sm text-emerald-200">{outcome}</p>
        )}
        {pending.length === 0 && (
          <p className="text-sm text-slate-500">Nothing waiting.</p>
        )}
        {pending.map((a) => (
          <div key={a.id} className="rounded-lg border border-slate-800 bg-slate-950/50 p-3">
            <div className="text-sm text-slate-200">{a.description}</div>
            <div className="mt-0.5 text-xs text-slate-500">
              Asked by {agentLabel(a.agent)} · {formatDate(a.created_at)}
            </div>
            <div className="mt-2.5 flex gap-2">
              <Button className="px-3 py-1.5 text-xs" loading={busy === a.id}
                      disabled={busy !== null} onClick={() => resolve(a.id, true)}>
                Approve &amp; run
              </Button>
              <Button variant="ghost" className="px-3 py-1.5 text-xs"
                      disabled={busy !== null} onClick={() => resolve(a.id, false)}>
                Decline
              </Button>
            </div>
          </div>
        ))}
      </CardContent>
    </Card>
  );
}
