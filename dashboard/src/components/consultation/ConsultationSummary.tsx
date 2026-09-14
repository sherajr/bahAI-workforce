import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Copy, Download, FileText, Loader2, Network, Trash2 } from "lucide-react";
import { api } from "../../lib/api";
import type { ConsultationCapabilities } from "../../lib/consultationTypes";
import { Button, Card, CardContent, ErrorNote, Modal } from "../ui";
import { ConceptGraphView } from "./ConceptGraph";
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
  sessionId, capabilities, onDeleted, onBack,
}: {
  sessionId: string;
  capabilities: ConsultationCapabilities;
  onDeleted: () => void;
  onBack: () => void;
}) {
  const qc = useQueryClient();
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [copied, setCopied] = useState(false);
  const [view, setView] = useState<"report" | "map" | "detail">("report");
  const [sourceTurns, setSourceTurns] = useState<string[] | null>(null);

  const { data, error } = useQuery({
    queryKey: ["consultation", sessionId],
    queryFn: () => api.getConsultation(sessionId),
  });

  // Archived: no live analysis is ever running, so a slow, occasional poll is
  // plenty, and it only runs while the map tab is actually open.
  const { data: graphData, isError: graphErrored } = useQuery({
    queryKey: ["consultation-graph", sessionId],
    queryFn: () => api.getConsultationGraph(sessionId),
    enabled: view === "map" || view === "report",
    refetchInterval: false,
  });

  const refresh = () => {
    void qc.invalidateQueries({ queryKey: ["consultation", sessionId] });
    void qc.invalidateQueries({ queryKey: ["consultation-graph", sessionId] });
  };

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
        {(["report", "map", "detail"] as const).map((v) => (
          <button
            key={v}
            onClick={() => setView(v)}
            className={`px-3 py-2 text-sm capitalize transition ${
              view === v
                ? "border-b-2 border-amber-400 text-slate-100"
                : "text-slate-500 hover:text-slate-300"
            }`}
          >
            {v === "report" ? "Report" : v === "map" ? "Concept map" : "Everything else"}
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
              {/* A preview, not a second interactive copy — full editing lives
                  in the Concept map tab (section 6: "a map preview within the
                  report screen"). */}
              {graphData && graphData.graph.nodes.length > 1 && (
                <Card>
                  <CardContent className="flex items-center justify-between gap-3 py-4">
                    <div className="flex items-center gap-2 text-sm text-slate-300">
                      <Network className="h-4 w-4 text-slate-500" />
                      {graphData.graph.nodes.length - 1} points on the concept map,
                      {" "}{graphData.graph.edges.filter((e) => !e.synthetic).length} connections
                      drawn between them.
                    </div>
                    <Button variant="secondary" className="text-xs" onClick={() => setView("map")}>
                      Open the map
                    </Button>
                  </CardContent>
                </Card>
              )}
            </>
          )}
        </div>
      )}

      {view === "map" && (
        graphErrored ? (
          <ErrorNote>The concept map could not be loaded.</ErrorNote>
        ) : !graphData ? (
          <p className="text-sm text-slate-400">Loading…</p>
        ) : (
          <div className="h-[70vh] min-h-[28rem]">
            <ConceptGraphView
              graph={graphData.graph}
              capabilities={capabilities}
              sessionId={sessionId}
              title={session.title}
              readOnly
              decisions={data.decisions}
              actions={data.action_items}
              onChanged={refresh}
              onShowSource={setSourceTurns}
            />
          </div>
        )
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

      {sourceTurns && (
        <Modal open onClose={() => setSourceTurns(null)} title="What was actually said">
          <div className="space-y-3">
            <p className="text-xs text-slate-500">
              The lines this came from. It shows what was said in the meeting — not
              that what was said is correct.
            </p>
            {(data.final_turns ?? data.turns)
              .filter((t) => sourceTurns.includes(String(t.id)))
              .map((t) => (
                <div key={t.id} className="rounded border border-slate-800 bg-slate-900/60 p-2">
                  <p className="text-xs font-semibold text-slate-400">
                    {t.role === "assistant" ? "Assistant" : t.speaker_label ?? "Participant"}
                  </p>
                  <p className="mt-0.5 text-sm text-slate-200">{t.text}</p>
                </div>
              ))}
            {!(data.final_turns ?? data.turns).some((t) => sourceTurns.includes(String(t.id))) && (
              <p className="text-sm text-slate-500">
                {data.transcript_deleted
                  ? "The transcript of this consultation was deleted; only the approved record remains."
                  : "Those lines are no longer in the transcript."}
              </p>
            )}
          </div>
        </Modal>
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
