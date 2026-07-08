import { useEffect, useState } from "react";
import { api } from "../lib/api";
import { Button, ErrorNote, Modal } from "./ui";

export function PersonalityModal({ open, onClose }: { open: boolean; onClose: () => void }) {
  const [text, setText] = useState("");
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    if (!open) return;
    setLoading(true);
    setError(null);
    setSaved(false);
    api
      .getPersonality()
      .then((r) => setText(r.custom_instructions))
      .catch((e) => setError(e instanceof Error ? e.message : "Could not load."))
      .finally(() => setLoading(false));
  }, [open]);

  const save = async () => {
    setSaving(true);
    setError(null);
    try {
      await api.setPersonality(text);
      setSaved(true);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not save.");
    } finally {
      setSaving(false);
    }
  };

  return (
    <Modal open={open} onClose={onClose} title="Abigail's personality & instructions" widthClassName="max-w-xl">
      <p className="mb-3 text-sm text-slate-400">
        Anything you write here is added to her instructions on every conversation — tone, things to
        always or never do, context she should keep in mind. Leave blank for default behavior.
      </p>
      {error && (
        <div className="mb-3">
          <ErrorNote>{error}</ErrorNote>
        </div>
      )}
      {loading ? (
        <p className="text-sm text-slate-500">Loading…</p>
      ) : (
        <>
          <textarea
            value={text}
            onChange={(e) => {
              setText(e.target.value);
              setSaved(false);
            }}
            rows={10}
            placeholder="e.g. Be more concise. Always ask before scheduling anything after 6pm."
            className="w-full resize-none rounded-lg border border-slate-700 bg-slate-900/80 px-3 py-2 text-sm text-slate-200 placeholder:text-slate-600 focus:border-amber-400/50 focus:outline-none"
          />
          <div className="mt-3 flex items-center gap-3">
            <Button onClick={save} loading={saving} disabled={saving}>
              Save
            </Button>
            {saved && <span className="text-xs text-emerald-300">Saved.</span>}
          </div>
        </>
      )}
    </Modal>
  );
}
