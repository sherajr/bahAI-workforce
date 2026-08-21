import { useQuery } from "@tanstack/react-query";
import { CheckCircle2, CircleDashed, Mic, Plus } from "lucide-react";
import { api } from "../../lib/api";
import type { ConsultationSession } from "../../lib/consultationTypes";
import { Button, Card, CardContent, ErrorNote } from "../ui";

/** Past consultations. Local and private, like everything else in this tab. */
export function ConsultationArchive({
  onOpen, onNew, canStart,
}: {
  onOpen: (id: string) => void;
  onNew: () => void;
  canStart: boolean;
}) {
  const { data, isLoading, error } = useQuery({
    queryKey: ["consultations"],
    queryFn: () => api.listConsultations(),
  });
  const sessions: ConsultationSession[] = data?.sessions ?? [];

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between gap-4">
        <div>
          <h2 className="font-display text-lg text-slate-100">Consultations</h2>
          <p className="text-sm text-slate-400">
            Every session is stored on this machine only, and can be deleted outright.
          </p>
        </div>
        <Button onClick={onNew} disabled={!canStart}>
          <Plus className="h-4 w-4" />
          New consultation
        </Button>
      </div>

      {error && <ErrorNote>{(error as Error).message}</ErrorNote>}
      {isLoading && <p className="text-sm text-slate-500">Loading…</p>}

      {!isLoading && sessions.length === 0 && (
        <Card>
          <CardContent className="py-10 text-center text-sm text-slate-400">
            <Mic className="mx-auto mb-3 h-6 w-6 text-slate-600" />
            No consultations yet. Abigail sits in, records what was said, keeps a map of
            the consultation as it develops — and it is never heard by anyone else.
          </CardContent>
        </Card>
      )}

      <div className="grid gap-3">
        {sessions.map((s) => (
          <button
            key={s.id}
            onClick={() => onOpen(s.id)}
            className="w-full rounded-xl border border-slate-800 bg-slate-900/70 px-5 py-4 text-left transition-colors hover:border-slate-700 hover:bg-slate-900"
          >
            <div className="flex items-start justify-between gap-4">
              <div className="min-w-0">
                <div className="truncate font-medium text-slate-100">{s.title}</div>
                {s.question && (
                  <div className="mt-0.5 truncate text-sm text-slate-400">{s.question}</div>
                )}
              </div>
              <div className="shrink-0 text-right text-xs text-slate-500">
                <div>{(s.started_at ?? s.created_at ?? "").slice(0, 16)}</div>
                <div className="mt-1 capitalize">{s.status}</div>
              </div>
            </div>
            <div className="mt-3 flex flex-wrap items-center gap-4 text-xs text-slate-500">
              <span>{s.turn_count ?? 0} turns</span>
              {s.decision_confirmed ? (
                <span className="inline-flex items-center gap-1.5 text-emerald-300">
                  <CheckCircle2 className="h-3.5 w-3.5" />
                  Decision confirmed
                </span>
              ) : (
                <span className="inline-flex items-center gap-1.5">
                  <CircleDashed className="h-3.5 w-3.5" />
                  No decision confirmed
                </span>
              )}
            </div>
          </button>
        ))}
      </div>
    </div>
  );
}
