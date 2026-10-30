import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { KeyRound } from "lucide-react";
import { api } from "../../lib/api";
import {
  clearConsultationOpen, getConsultationUi, patchConsultationUi, peekConsultationOpen,
} from "../../lib/settings";
import { Card, CardContent, ErrorNote } from "../ui";
import { ConsultationArchive } from "./ConsultationArchive";
import { ConsultationCloseout } from "./ConsultationCloseout";
import { ConsultationSetup } from "./ConsultationSetup";
import { ConsultationSummary } from "./ConsultationSummary";
import { LiveConsultationSession } from "./LiveConsultationSession";

type View = "archive" | "setup" | "live" | "closeout" | "summary" | "opening";

/**
 * The Consultation tab.
 *
 * The view and the open session persist (the panel unmounts when Sheraj looks
 * at another tab), but a LIVE connection deliberately does not resume itself:
 * a realtime session needs a fresh credential and a fresh microphone
 * permission, and neither should ever be re-taken silently.
 */
export function ConsultationPanel() {
  const saved = getConsultationUi();
  // A link from Home to one particular consultation beats whatever this tab
  // showed last: showing the old view first would put the wrong meeting on
  // screen for a moment, under a heading that says it is waiting for you.
  const [linked] = useState(() => peekConsultationOpen());
  const [view, setView] = useState<View>(
    linked ? "opening"
      : saved.view === "setup" || saved.view === "summary" || saved.view === "closeout"
        ? (saved.view as View) : "archive");
  const [sessionId, setSessionId] = useState<string | null>(saved.sessionId);
  const [autoStart, setAutoStart] = useState(false);
  const [openError, setOpenError] = useState<string | null>(null);

  const { data: capabilities, error } = useQuery({
    queryKey: ["consultation-capabilities"],
    queryFn: () => api.consultationCapabilities(),
    staleTime: 60_000,
  });

  useEffect(() => {
    patchConsultationUi({ view, sessionId });
  }, [view, sessionId]);

  /**
   * `review` is how Home's "Needs your decision" opens a meeting: an ended one
   * whose record nobody has approved and that was never closed out goes to the
   * closeout review, because that is the one screen where the decisions get
   * confirmed and the record approved. Opening it on the report instead is
   * what used to leave nothing on screen to decide.
   */
  const openSession = async (id: string, purpose: "review" | "open" = "open") => {
    setOpenError(null);
    try {
      const detail = await api.getConsultation(id);
      const s = detail.session;
      setSessionId(id);
      setAutoStart(false);
      if (s.status !== "ended") setView("live");
      else if (purpose === "review" && !s.closeout_at && !detail.record?.approved) {
        setView("closeout");
      } else setView("summary");
    } catch (e) {
      setOpenError(`That consultation could not be opened: ${(e as Error).message}`);
      setView("archive");
    }
  };

  useEffect(() => {
    if (!linked) return;
    clearConsultationOpen();
    void openSession(linked.sessionId, linked.purpose);
    // Once, on mount: the link is a single hand-off, not a standing setting.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  if (error) return <ErrorNote>{(error as Error).message}</ErrorNote>;
  if (!capabilities) return <p className="text-sm text-slate-400">Loading…</p>;

  const canStart = capabilities.realtime_available;

  return (
    <div className="flex h-full min-h-0 flex-col gap-4">
      {!canStart && (
        <Card className="border-amber-400/40">
          <CardContent className="flex items-start gap-3 py-4 text-sm text-amber-100">
            <KeyRound className="mt-0.5 h-4 w-4 shrink-0 text-amber-300" />
            <div>
              <div className="font-medium">{capabilities.missing_key_message}</div>
              <div className="mt-1 text-xs text-amber-200/70">
                Past consultations still open and read normally.
              </div>
            </div>
          </CardContent>
        </Card>
      )}

      {canStart && capabilities.reasoning_note && (
        <Card className="border-amber-400/40">
          <CardContent className="py-3 text-sm text-amber-100">
            {capabilities.reasoning_note} Until then the transcript is still recorded, but
            the consultation map will not be built.
          </CardContent>
        </Card>
      )}

      {openError && <ErrorNote>{openError}</ErrorNote>}

      {view === "opening" && (
        <p className="text-sm text-slate-400">Opening the consultation…</p>
      )}

      {view === "archive" && (
        <ConsultationArchive
          canStart={canStart}
          onNew={() => { setSessionId(null); setView("setup"); }}
          onOpen={(id) => void openSession(id)}
        />
      )}

      {view === "setup" && (
        <ConsultationSetup
          capabilities={capabilities}
          onCancel={() => setView("archive")}
          onStarted={(id) => { setSessionId(id); setAutoStart(true); setView("live"); }}
        />
      )}

      {view === "live" && sessionId && (
        <LiveConsultationSession
          key={sessionId}
          sessionId={sessionId}
          capabilities={capabilities}
          autoStart={autoStart}
          onEnded={() => { setAutoStart(false); setView("closeout"); }}
          onBack={() => setView("archive")}
        />
      )}

      {view === "summary" && sessionId && (
        <ConsultationSummary
          sessionId={sessionId}
          capabilities={capabilities}
          onDeleted={() => { setSessionId(null); setView("archive"); }}
          onBack={() => setView("archive")}
          onReview={() => setView("closeout")}
        />
      )}

      {view === "closeout" && sessionId && (
        <ConsultationCloseout
          sessionId={sessionId}
          capabilities={capabilities}
          onDone={() => setView("summary")}
          onSkip={() => setView("summary")}
        />
      )}

      {(view === "live" || view === "summary" || view === "closeout") && !sessionId && (
        <ConsultationArchive
          canStart={canStart}
          onNew={() => setView("setup")}
          onOpen={(id) => void openSession(id)}
        />
      )}

      <p className="pt-2 text-[11px] leading-relaxed text-slate-600">
        {capabilities.assistant_name} listens constantly, understands continuously and
        speaks rarely. She is not the chairman, she never records a decision the group has
        not confirmed, and in a meeting she knows nothing of your private world.{" "}
        {/* This sentence used to claim everything said here never left the
            machine, which was simply untrue: the microphone goes to OpenAI to be
            transcribed. The distinction that matters is between where audio is
            PROCESSED and where the record is STORED, and it is now drawn
            everywhere it is made (rule 94). */}
        Speech is sent to OpenAI to be transcribed; the record is stored on this machine.
      </p>
    </div>
  );
}
