import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { KeyRound } from "lucide-react";
import { api } from "../../lib/api";
import { getConsultationUi, patchConsultationUi } from "../../lib/settings";
import { Card, CardContent, ErrorNote } from "../ui";
import { ConsultationArchive } from "./ConsultationArchive";
import { ConsultationSetup } from "./ConsultationSetup";
import { ConsultationSummary } from "./ConsultationSummary";
import { LiveConsultationSession } from "./LiveConsultationSession";

type View = "archive" | "setup" | "live" | "summary";

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
  const [view, setView] = useState<View>(
    saved.view === "setup" || saved.view === "summary" ? (saved.view as View) : "archive");
  const [sessionId, setSessionId] = useState<string | null>(saved.sessionId);
  const [autoStart, setAutoStart] = useState(false);

  const { data: capabilities, error } = useQuery({
    queryKey: ["consultation-capabilities"],
    queryFn: () => api.consultationCapabilities(),
    staleTime: 60_000,
  });

  useEffect(() => {
    patchConsultationUi({ view, sessionId });
  }, [view, sessionId]);

  const openSession = async (id: string) => {
    setSessionId(id);
    const detail = await api.getConsultation(id);
    setAutoStart(false);
    setView(detail.session.status === "ended" ? "summary" : "live");
  };

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
          onEnded={() => { setAutoStart(false); setView("summary"); }}
          onBack={() => setView("archive")}
        />
      )}

      {view === "summary" && sessionId && (
        <ConsultationSummary
          sessionId={sessionId}
          onDeleted={() => { setSessionId(null); setView("archive"); }}
          onBack={() => setView("archive")}
        />
      )}

      {(view === "live" || view === "summary") && !sessionId && (
        <ConsultationArchive
          canStart={canStart}
          onNew={() => setView("setup")}
          onOpen={(id) => void openSession(id)}
        />
      )}

      <p className="pt-2 text-[11px] leading-relaxed text-slate-600">
        {capabilities.assistant_name} listens constantly, understands continuously and
        speaks rarely. She is not the chairman, she never records a decision the group has
        not confirmed, and in a meeting she knows nothing of your private world. Everything
        said here stays on this machine.
      </p>
    </div>
  );
}
