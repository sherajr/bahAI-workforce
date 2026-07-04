import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "../lib/api";
import { Button } from "./ui";

/**
 * Shown when a background job's status is "waiting_for_input" — the Reviewer
 * has asked Sheraj for guidance after consultation round 2, and the pipeline
 * is genuinely paused (the worker thread is blocked) until this responds.
 */
export function ConsultationPause({ jobId, prompt }: { jobId: string; prompt: string }) {
  const queryClient = useQueryClient();
  const [text, setText] = useState("");

  const respond = useMutation({
    mutationFn: (t: string) => api.respondToJob(jobId, t),
    onSuccess: () => {
      setText("");
      queryClient.invalidateQueries({ queryKey: ["job", jobId] });
    },
  });

  return (
    <div className="space-y-2 rounded-lg border border-amber-400/30 bg-amber-950/20 p-3">
      <p className="text-sm text-amber-200">{prompt}</p>
      <div className="flex gap-2">
        <input
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && !respond.isPending && respond.mutate(text)}
          placeholder="Type guidance, or leave blank and send to continue..."
          className="flex-1 rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-slate-100 placeholder-slate-600"
        />
        <Button loading={respond.isPending} onClick={() => respond.mutate(text)}>
          Send
        </Button>
      </div>
    </div>
  );
}
