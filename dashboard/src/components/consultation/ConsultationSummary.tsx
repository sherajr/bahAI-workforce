import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Check, CheckCircle2, ClipboardCheck, Copy, Download, FileText, Loader2, Network, Trash2,
} from "lucide-react";
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
  sessionId, capabilities, onDeleted, onBack, onReview,
}: {
  sessionId: string;
  capabilities: ConsultationCapabilities;
  onDeleted: () => void;
  onBack: () => void;
  /** Back to the closeout review, for a meeting that never had one. */
  onReview: () => void;
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

  // The recovery path for an incomplete closing pass (section 4) — the note
  // persists on the session, so it is still here after a reload, not only in
  // the one response that used to be thrown away when End navigated here.
  const finishAnalysis = useMutation({
    mutationFn: () => api.finishConsultationAnalysis(sessionId),
    onSuccess: () => refresh(),
  });

  const [showApprovedMap, setShowApprovedMap] = useState(false);
  const { data: approvedGraphData, isError: approvedGraphErrored } = useQuery({
    queryKey: ["consultation-graph-approved", sessionId],
    queryFn: () => api.getApprovedConsultationGraph(sessionId),
    enabled: showApprovedMap,
    refetchInterval: false,
  });

  // Reading the record before approving it (rule 102). The preview is free and
  // changes nothing; approving is bound to the revision the preview came from,
  // so an edit in between is refused with the reason instead of approved unread.
  const [approving, setApproving] = useState(false);
  const preview = useQuery({
    queryKey: ["consultation-record-preview", sessionId],
    queryFn: () => api.previewConsultationRecord(sessionId),
    enabled: approving,
    refetchInterval: false,
  });
  const approve = useMutation({
    mutationFn: (revision: number) => api.approveConsultationRecord(sessionId, revision),
    onSuccess: () => {
      setApproving(false);
      refresh();
      // Home's "Needs your decision" and the archive both count this.
      void qc.invalidateQueries({ queryKey: ["home"] });
      void qc.invalidateQueries({ queryKey: ["consultations"] });
    },
    // A refusal means the record moved: show the new words, not the old ones.
    onError: () => void preview.refetch(),
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
  const record = data.record;
  // Never closed out: the review is where decisions get confirmed, so send the
  // person there rather than approving a record whose outcome nobody chose.
  const needsReview = !!record && !record.approved && !session.closeout_at;
  const needsApproval = !!record && ((!record.approved && !!session.closeout_at)
    || record.stale);

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

      {/* Persistent — this is a field on the session, not a one-off response
          note, precisely so it survives the navigation from End to here
          (section 4). It clears itself once `finish-analysis` actually
          succeeds. */}
      {data.final_pass_note && (
        <ErrorNote>
          <div className="flex flex-wrap items-center justify-between gap-3">
            <span>The closing analysis did not finish: {data.final_pass_note}</span>
            <Button variant="secondary" className="text-xs" disabled={finishAnalysis.isPending}
                    onClick={() => void finishAnalysis.mutate()}>
              {finishAnalysis.isPending ? (
                <><Loader2 className="mr-1 h-3.5 w-3.5 animate-spin" /> Finishing…</>
              ) : "Finish analysis"}
            </Button>
          </div>
        </ErrorNote>
      )}

      {/* What is waiting on you, above the tabs so no tab can hide it. Until
          2026-09-29 a meeting Home listed under "Needs your decision" opened
          here with nothing on the page to decide: approving was only possible
          on the closeout screen, shown once, and "Finish later" lost it. */}
      {(needsReview || needsApproval) && (
        <Card className="border-amber-400/40">
          <CardContent className="flex flex-wrap items-center justify-between gap-3 py-4">
            <div className="flex min-w-0 flex-1 items-start gap-3 text-sm text-amber-100">
              <ClipboardCheck className="mt-0.5 h-4 w-4 shrink-0 text-amber-300" />
              <div>
                <div className="font-medium">
                  {needsReview ? "This record is waiting for your approval"
                    : record?.stale ? "The approved record is out of date"
                      : "This record has not been approved"}
                </div>
                <div className="mt-0.5 text-xs text-amber-200/70">
                  {needsReview
                    ? "Say how the meeting ended, confirm anything that was decided, "
                      + "record who agreed to do what, then approve it. Until then it is "
                      + "a draft."
                    : record?.stale
                      ? "It has been corrected since it was approved. Read the current "
                        + "version and approve it again."
                      : "Read it through and approve it. Until then it is a draft."}
                </div>
              </div>
            </div>
            {needsReview ? (
              <Button onClick={onReview}>Review and approve</Button>
            ) : (
              <Button onClick={() => { approve.reset(); setApproving(true); }}>
                Read and approve
              </Button>
            )}
          </CardContent>
        </Card>
      )}
      {record?.approved && !record.stale && record.approved_at && (
        <p className="flex items-center gap-1.5 text-xs text-emerald-300/80">
          <CheckCircle2 className="h-3.5 w-3.5" />
          Record approved {record.approved_at.slice(0, 16)}.
        </p>
      )}

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
          <div className="space-y-2">
            {/* The map has never had a way to correct a connection once the
                meeting ended — everything else here (decisions, actions,
                the transcript) could already be corrected by hand, but this
                one screen offered no flow to it at all (section 2). It is the
                SAME editing surface the live view uses; nothing here is a
                second way to write the record. */}
            {data.has_approved_graph && data.record?.stale && (
              <Card>
                <CardContent className="flex flex-wrap items-center justify-between gap-3 py-3 text-sm text-slate-300">
                  <span>
                    This map has changed since the record was approved
                    {data.record?.approved_at ? ` on ${data.record.approved_at.slice(0, 16)}` : ""}.
                  </span>
                  <Button variant="secondary" className="text-xs"
                          onClick={() => setShowApprovedMap(true)}>
                    View the approved map
                  </Button>
                </CardContent>
              </Card>
            )}
            <div className="h-[70vh] min-h-[28rem]">
              <ConceptGraphView
                graph={graphData.graph}
                capabilities={capabilities}
                sessionId={sessionId}
                title={session.title}
                readOnly={false}
                decisions={data.decisions}
                actions={data.action_items}
                onChanged={refresh}
                onShowSource={setSourceTurns}
              />
            </div>
          </div>
        )
      )}

      <Modal open={showApprovedMap} onClose={() => setShowApprovedMap(false)}
             title="The approved map" widthClassName="max-w-5xl">
        {approvedGraphErrored ? (
          <ErrorNote>The approved map could not be loaded.</ErrorNote>
        ) : !approvedGraphData ? (
          <p className="text-sm text-slate-400">Loading…</p>
        ) : (
          <div className="space-y-2">
            <p className="text-xs text-slate-500">
              Approved {(approvedGraphData.approved_at ?? "").slice(0, 16)}. This is an archive
              — it does not change as the map above is corrected.
            </p>
            <div className="h-[60vh] min-h-[24rem]">
              <ConceptGraphView
                graph={approvedGraphData.graph}
                capabilities={capabilities}
                sessionId={sessionId}
                title={session.title}
                readOnly
                decisions={data.decisions}
                actions={data.action_items}
                onChanged={() => {}}
                onShowSource={setSourceTurns}
              />
            </div>
          </div>
        )}
      </Modal>

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

      <Modal open={approving} onClose={() => setApproving(false)}
             title="Approve the record" widthClassName="max-w-3xl">
        <div className="space-y-4 text-sm text-slate-300">
          <p className="text-xs text-slate-500">
            This is exactly what will be approved. Approving puts your name to it as the
            record of this meeting; it becomes what is exported, and what a gathering
            can draw its decisions and commitments from.
          </p>
          {preview.isError && (
            <ErrorNote>The record could not be read: {(preview.error as Error).message}</ErrorNote>
          )}
          {!preview.data && !preview.isError && (
            <p className="text-slate-400">Reading…</p>
          )}
          {preview.data && (
            <div className="max-h-[55vh] overflow-y-auto rounded-lg border border-slate-800 bg-slate-950/60 p-4">
              <Markdown text={preview.data.preview} />
            </div>
          )}
          {approve.error && <ErrorNote>{(approve.error as Error).message}</ErrorNote>}
          <div className="flex justify-end gap-2">
            <Button variant="secondary" onClick={() => setApproving(false)}>Not yet</Button>
            <Button disabled={!preview.data || preview.isFetching}
                    loading={approve.isPending}
                    onClick={() => preview.data
                      && approve.mutate(preview.data.record.revision)}>
              <Check className="h-4 w-4" />
              Approve this record
            </Button>
          </div>
        </div>
      </Modal>

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
