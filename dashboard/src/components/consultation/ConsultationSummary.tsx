import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Copy, Download, Trash2 } from "lucide-react";
import { api } from "../../lib/api";
import { Button, Card, CardContent, CardHeader, CardTitle, ErrorNote, Modal } from "../ui";
import { ConsultationMap } from "./ConsultationMap";

/** The record of a finished consultation. */
export function ConsultationSummary({
  sessionId, onDeleted, onBack,
}: {
  sessionId: string;
  onDeleted: () => void;
  onBack: () => void;
}) {
  const qc = useQueryClient();
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [copied, setCopied] = useState(false);

  const { data, error } = useQuery({
    queryKey: ["consultation", sessionId],
    queryFn: () => api.getConsultation(sessionId),
  });

  const refresh = () => void qc.invalidateQueries({ queryKey: ["consultation", sessionId] });

  const remove = useMutation({
    mutationFn: () => api.deleteConsultation(sessionId),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["consultations"] });
      onDeleted();
    },
  });

  const copySummary = async () => {
    const res = await fetch(api.consultationExportUrl(sessionId));
    await navigator.clipboard.writeText(await res.text());
    setCopied(true);
    window.setTimeout(() => setCopied(false), 2000);
  };

  if (error) return <ErrorNote>{(error as Error).message}</ErrorNote>;
  if (!data) return <p className="text-sm text-slate-400">Loading…</p>;

  const { session, state, turns, decisions, action_items, writings } = data;
  const confirmed = decisions.find((d) => d.status === "confirmed");

  return (
    <div className="space-y-4 pb-8">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <h2 className="font-display text-lg text-slate-100">{session.title}</h2>
          {session.question && <p className="text-sm text-slate-400">{session.question}</p>}
          <p className="mt-1 text-xs text-slate-500">
            {(session.started_at ?? session.created_at ?? "").slice(0, 16)}
            {session.ended_at ? ` — ended ${session.ended_at.slice(11, 16)}` : ""}
            {" · "}{turns.length} turns
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          <Button variant="secondary" onClick={onBack}>Back</Button>
          <Button variant="secondary" onClick={() => void copySummary()}>
            <Copy className="h-4 w-4" />
            {copied ? "Copied" : "Copy summary"}
          </Button>
          <a href={api.consultationExportUrl(sessionId)} target="_blank" rel="noreferrer">
            <Button variant="secondary">
              <Download className="h-4 w-4" />
              Markdown
            </Button>
          </a>
          <Button variant="danger" onClick={() => setConfirmDelete(true)}>
            <Trash2 className="h-4 w-4" />
            Delete session
          </Button>
        </div>
      </div>

      {data.note && (
        <div className="rounded-lg border border-slate-800 bg-slate-900/60 px-4 py-2 text-xs text-slate-400">
          {data.note}
        </div>
      )}

      {!confirmed && (
        <Card className="border-slate-700">
          <CardContent className="py-4 text-sm text-slate-300">
            <span className="font-medium text-slate-100">No final decision was confirmed.</span>{" "}
            That is recorded as it happened; nothing was inferred.
          </CardContent>
        </Card>
      )}

      <ConsultationMap
        state={state}
        decisions={decisions}
        actions={action_items}
        writings={writings}
        onConfirmDecision={(id) => {
          void api.confirmConsultationDecision(sessionId, id).then(refresh);
        }}
        onRejectDecision={(id) => {
          void api.rejectConsultationDecision(sessionId, id).then(refresh);
        }}
        onToggleAction={(id, status) => {
          void api.setConsultationActionStatus(sessionId, id, status).then(refresh);
        }}
      />

      <Card>
        <CardHeader><CardTitle>Transcript</CardTitle></CardHeader>
        <CardContent className="max-h-[28rem] space-y-3 overflow-y-auto">
          {turns.length === 0 && <p className="text-sm text-slate-500">Nothing was recorded.</p>}
          {turns.map((t) => (
            <div key={t.id}>
              <div className={`text-xs font-semibold ${
                t.role === "assistant" ? "text-amber-300" : "text-slate-400"
              }`}>
                {t.role === "assistant" ? "Abigail" : t.speaker_label ?? "Participant"}
              </div>
              <p className="text-sm leading-relaxed text-slate-300">{t.text}</p>
            </div>
          ))}
        </CardContent>
      </Card>

      <Modal open={confirmDelete} onClose={() => setConfirmDelete(false)}
             title="Delete this consultation?">
        <div className="space-y-4 text-sm text-slate-300">
          <p>
            This removes the transcript, the consultation map, everything the assistant
            noticed, the decisions and the action items. There is no copy anywhere else.
          </p>
          {remove.error && <ErrorNote>{(remove.error as Error).message}</ErrorNote>}
          <div className="flex justify-end gap-2">
            <Button variant="secondary" onClick={() => setConfirmDelete(false)}>Keep it</Button>
            <Button variant="danger" onClick={() => remove.mutate()} loading={remove.isPending}>
              Delete permanently
            </Button>
          </div>
        </div>
      </Modal>
    </div>
  );
}
