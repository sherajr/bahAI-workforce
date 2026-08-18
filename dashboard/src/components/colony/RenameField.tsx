import { useEffect, useRef, useState } from "react";
import { Check, Pencil, X } from "lucide-react";

interface Props {
  /** What it is called now. */
  name: string;
  /** Saves the new name. Rejecting shows the message next to the field. */
  onSave: (name: string) => Promise<unknown>;
  /** Shown while the field is closed, under the name. */
  subtitle?: string;
  /** Blocked with this reason instead of offering a pencil. */
  disabledReason?: string;
  label?: string;
}

/**
 * The heading of a drawer, with a quiet pencil that turns it into a field.
 *
 * Shared by the friend and grouping drawers because the fiddly parts are the
 * same in both and are the parts that go wrong: the draft has to reset when
 * the drawer is pointed at someone else (otherwise you open a second person
 * and find the first one's name in the box, one Enter away from renaming the
 * wrong light), an empty name has to be refused on the way in as well as at
 * the store, and a rejected save has to say why rather than closing quietly.
 */
export function RenameField({
  name, onSave, subtitle, disabledReason, label = "Rename",
}: Props) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(name);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const input = useRef<HTMLInputElement | null>(null);

  // Whoever the drawer is showing now — not whoever it was showing when the
  // field was last opened.
  useEffect(() => {
    setEditing(false);
    setDraft(name);
    setError(null);
  }, [name]);

  useEffect(() => {
    if (editing) input.current?.select();
  }, [editing]);

  const commit = async () => {
    const next = draft.trim();
    if (!next) {
      setError("A name cannot be empty.");
      return;
    }
    if (next === name) {
      setEditing(false);
      return;
    }
    setSaving(true);
    setError(null);
    try {
      await onSave(next);
      setEditing(false);
    } catch (e) {
      // The API client prefixes the HTTP status; the reason is the part that
      // helps, and Sheraj should never be shown a status code.
      const why = ((e as Error).message || "").replace(/^\d{3}:\s*/, "");
      setError(why || "That name was not saved.");
    } finally {
      setSaving(false);
    }
  };

  const cancel = () => {
    setDraft(name);
    setError(null);
    setEditing(false);
  };

  if (!editing) {
    return (
      <div className="mb-3">
        <div className="flex items-start gap-1.5">
          <h2 className="font-display text-lg leading-tight text-slate-100">{name}</h2>
          {!disabledReason && (
            <button
              type="button"
              onClick={() => setEditing(true)}
              title={label}
              aria-label={`${label} ${name}`}
              className="mt-1 shrink-0 rounded p-0.5 text-slate-600 hover:text-amber-200"
            >
              <Pencil className="h-3.5 w-3.5" />
            </button>
          )}
        </div>
        {subtitle && <p className="text-xs text-slate-500">{subtitle}</p>}
        {disabledReason && (
          <p className="mt-0.5 text-[11px] text-slate-600">{disabledReason}</p>
        )}
      </div>
    );
  }

  return (
    <div className="mb-3">
      <div className="flex items-center gap-1.5">
        <input
          ref={input}
          value={draft}
          autoFocus
          disabled={saving}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") void commit();
            if (e.key === "Escape") cancel();
          }}
          className="min-w-0 flex-1 rounded-md border border-slate-600 bg-slate-950 px-2 py-1.5
                     text-sm text-slate-100 disabled:opacity-50"
        />
        <button
          type="button"
          onClick={() => void commit()}
          disabled={saving}
          title="Save"
          aria-label="Save the new name"
          className="shrink-0 rounded-md border border-emerald-400/40 bg-emerald-400/10 p-1.5
                     text-emerald-200 disabled:opacity-40"
        >
          <Check className="h-3.5 w-3.5" />
        </button>
        <button
          type="button"
          onClick={cancel}
          disabled={saving}
          title="Cancel"
          aria-label="Leave the name as it is"
          className="shrink-0 rounded-md border border-slate-700 p-1.5 text-slate-400
                     disabled:opacity-40"
        >
          <X className="h-3.5 w-3.5" />
        </button>
      </div>
      <p className="mt-1 text-[11px] text-slate-600">
        {saving ? "Saving…" : "Enter to save, Escape to leave it."}
      </p>
      {error && <p className="mt-0.5 text-[11px] text-rose-300">{error}</p>}
    </div>
  );
}
