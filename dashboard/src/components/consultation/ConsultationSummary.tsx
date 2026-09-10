import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Copy, Download, FileText, Loader2, Trash2 } from "lucide-react";
import { api } from "../../lib/api";
import { Button, Card, CardContent, ErrorNote, Modal } from "../ui";
import { ConsultationDetailPanel } from "./ConsultationDetail";
import { Markdown } from "./Markdown";

/**
 * The record of a finished consultation.
 *
 * The REPORT is the front page — one readable thing you can send to someone who
 * was not there. The working map and the transcript are behind the Detail tab
 * (owner ask 2026-08-25: "far too long to read"). Nothing was thrown away; it
 * stopped being the first thing you see.
 */
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
  const [view, setView] = useState<"report" | "detail">("report");

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

  const buildReport = useMutation({
    mutationFn: () => api.makeConsultationReport(sessionId),
    onSuccess: () => refresh(),
  });

  const diarize = useMutation({
    mutationFn: () => api.diarizeConsultation(sessionId),
    onSuccess: () => refresh(),
  });

  const mapSpeaker = useMutation({
    mutationFn: ({ id, key }: { id: string; key: string | null }) =>
      api.mapConsultationSpeaker(sessionId, id, key),
    onSuccess: () => refresh(),
  });

  const copyReport = async (text: string) => {
    await navigator.clipboard.writeText(text);
    setCopied(true);
    window.setTimeout(() => setCopied(false), 2000);
  };

  const download = (text: string, title: string) => {
    // A Blob URL rather than a data: URI so a long report is not capped by URL
    // length, and revoked straight after so the page does not leak object URLs.
    const blob = new Blob([text], { type: "text/markdown;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `${title.replace(/[^\w \-]+/g, "").trim() || "consultation"}.md`;
    document.body.appendChild(a);
    a.click();
    a.remove();
    window.setTimeout(() => URL.revokeObjectURL(url), 1000);
  };

  if (error) return <ErrorNote>{(error as Error).message}</ErrorNote>;
  if (!data) return <p className="text-sm text-slate-400">Loading…</p>;

  const { session, turns, final_turns } = data;
  const report = data.report || "";

  return (
    <div className="space-y-4 pb-8">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <h2 className="font-display text-lg text-slate-100">{session.title}</h2>
          {session.question && <p className="text-sm text-slate-400">{session.question}</p>}
          <p className="mt-1 text-xs text-slate-500">
            {(session.started_at ?? session.created_at ?? "").slice(0, 16)}
            {session.ended_at ? ` — ended ${session.ended_at.slice(11, 16)}` : ""}
            {" · "}{(final_turns ?? turns).length} turns
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          <Button variant="secondary" onClick={onBack}>Back</Button>
          {report && (
            <>
              <Button variant="secondary" onClick={() => void copyReport(report)}>
                <Copy className="h-4 w-4" />
                {copied ? "Copied" : "Copy report"}
              </Button>
              <Button variant="secondary" onClick={() => download(report, session.title)}>
                <Download className="h-4 w-4" />
                Download
              </Button>
            </>
          )}
          <Button variant="danger" onClick={() => setConfirmDelete(true)}>
            <Trash2 className="h-4 w-4" />
            Delete session
          </Button>
        </div>
      </div>

      <div className="flex gap-1 border-b border-slate-800">
        {(["report", "detail"] as const).map((v) => (
          <button
            key={v}
            onClick={() => setView(v)}
            className={`px-3 py-2 text-sm capitalize transition ${
              view === v
                ? "border-b-2 border-amber-400 text-slate-100"
                : "text-slate-500 hover:text-slate-300"
            }`}
          >
            {v === "report" ? "Report" : "Everything else"}
          </button>
        ))}
      </div>

      {view === "report" && (
        <div className="space-y-3">
          {buildReport.error && <ErrorNote>{(buildReport.error as Error).message}</ErrorNote>}
          {!report && (
            <Card>
              <CardContent className="space-y-3 py-6 text-center">
                <FileText className="mx-auto h-8 w-8 text-slate-600" />
                <p className="text-sm text-slate-400">
                  No report has been written for this consultation yet.
                </p>
                <Button onClick={() => buildReport.mutate()} disabled={buildReport.isPending}>
                  {buildReport.isPending ? (
                    <><Loader2 className="mr-2 h-4 w-4 animate-spin" /> Writing it…</>
                  ) : "Write the report"}
                </Button>
                <p className="mx-auto max-w-md text-xs text-slate-500">
                  The decision, the action items and any passages are copied exactly from
                  the record. The summarising parts are written from the working notes.
                </p>
              </CardContent>
            </Card>
          )}
          {report && (
            <>
              <Card>
                <CardContent className="py-5">
                  <Markdown text={report} />
                </CardContent>
              </Card>
              <div className="flex items-center justify-between gap-3">
                <p className="text-xs text-slate-500">
                  {session.report_at ? `Written ${session.report_at.slice(0, 16)}.` : ""}
                </p>
                <Button variant="ghost" onClick={() => buildReport.mutate()}
                        disabled={buildReport.isPending} className="text-xs">
                  {buildReport.isPending ? "Rewriting…" : "Write it again"}
                </Button>
              </div>
            </>
          )}
        </div>
      )}

      {view === "detail" && (
        <ConsultationDetailPanel
          detail={data}
          diarizing={diarize.isPending}
          error={diarize.error ? (diarize.error as Error).message : undefined}
          onDiarize={() => diarize.mutate()}
          onMapSpeaker={(id, key) => mapSpeaker.mutate({ id, key })}
        />
      )}

      <Modal open={confirmDelete} onClose={() => setConfirmDelete(false)}
             title="Delete this consultation?">
        <div className="space-y-4 text-sm text-slate-300">
          {/* Literally true, and no more than that (rule 94). "There is no copy
              anywhere else" was not something this application could know: it
              cannot reach a report somebody downloaded, a page somebody copied,
              or anything OpenAI holds under its own terms. What it can promise
              is what it manages itself, so that is what it says. */}
          <p>
            This removes everything this app holds for this consultation — the transcript,
            the consultation map, everything the assistant noticed, the decisions, the
            action items, the report and any recording. It cannot remove a copy you have
            already downloaded or shared, and it cannot erase anything the transcription
            service holds under its own terms.
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
