import { useEffect, useState } from "react";
import { Trash2 } from "lucide-react";
import { api } from "../lib/api";
import type { NoteRow } from "../lib/types";
import { Button, ErrorNote, Modal } from "./ui";

function NoteCard({ note, onSaved, onDeleted }: { note: NoteRow; onSaved: () => void; onDeleted: () => void }) {
  const [content, setContent] = useState(note.content);
  const [saving, setSaving] = useState(false);
  const [dirty, setDirty] = useState(false);

  const save = async () => {
    setSaving(true);
    try {
      await api.saveNote(note.name, content);
      setDirty(false);
      onSaved();
    } finally {
      setSaving(false);
    }
  };

  const del = async () => {
    if (!confirm(`Delete the "${note.name}" note? This can't be undone.`)) return;
    await api.deleteNote(note.name);
    onDeleted();
  };

  return (
    <div className="rounded-lg border border-slate-800 bg-slate-900/40 p-3">
      <div className="mb-2 flex items-center justify-between">
        <span className="text-sm font-medium text-slate-200">{note.name}</span>
        <button onClick={del} className="text-slate-500 hover:text-rose-400" title="Delete note">
          <Trash2 className="h-3.5 w-3.5" />
        </button>
      </div>
      <textarea
        value={content}
        onChange={(e) => {
          setContent(e.target.value);
          setDirty(true);
        }}
        rows={4}
        className="w-full resize-none rounded-lg border border-slate-700 bg-slate-900/80 px-3 py-2 text-xs text-slate-200 focus:border-amber-400/50 focus:outline-none"
      />
      {dirty && (
        <Button onClick={save} loading={saving} disabled={saving} className="mt-2" variant="secondary">
          Save
        </Button>
      )}
    </div>
  );
}

export function NotesModal({ open, onClose }: { open: boolean; onClose: () => void }) {
  const [notes, setNotes] = useState<NoteRow[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [newName, setNewName] = useState("");
  const [newContent, setNewContent] = useState("");

  const refresh = () => {
    setLoading(true);
    api
      .getNotes()
      .then((r) => setNotes(r.notes))
      .catch((e) => setError(e instanceof Error ? e.message : "Could not load notes."))
      .finally(() => setLoading(false));
  };

  useEffect(() => {
    if (open) refresh();
  }, [open]);

  const addNote = async () => {
    const name = newName.trim();
    const content = newContent.trim();
    if (!name || !content) return;
    await api.saveNote(name, content);
    setNewName("");
    setNewContent("");
    refresh();
  };

  return (
    <Modal open={open} onClose={onClose} title="Abigail's notes" widthClassName="max-w-xl">
      <p className="mb-3 text-sm text-slate-400">
        What she remembers about you, grouped by category. She reads all of this every conversation.
      </p>
      {error && (
        <div className="mb-3">
          <ErrorNote>{error}</ErrorNote>
        </div>
      )}
      {loading ? (
        <p className="text-sm text-slate-500">Loading…</p>
      ) : (
        <div className="space-y-3">
          {notes.length === 0 && <p className="text-sm text-slate-500">No notes yet.</p>}
          {notes.map((n) => (
            <NoteCard key={n.name} note={n} onSaved={refresh} onDeleted={refresh} />
          ))}
          <div className="rounded-lg border border-dashed border-slate-700 p-3">
            <div className="mb-2 text-xs uppercase tracking-wide text-slate-500">Add a note</div>
            <input
              value={newName}
              onChange={(e) => setNewName(e.target.value)}
              placeholder="Category (e.g. family, preferences)"
              className="mb-2 w-full rounded-lg border border-slate-700 bg-slate-900/80 px-3 py-1.5 text-sm text-slate-200 placeholder:text-slate-600 focus:border-amber-400/50 focus:outline-none"
            />
            <textarea
              value={newContent}
              onChange={(e) => setNewContent(e.target.value)}
              rows={3}
              placeholder="What should she remember?"
              className="mb-2 w-full resize-none rounded-lg border border-slate-700 bg-slate-900/80 px-3 py-2 text-sm text-slate-200 placeholder:text-slate-600 focus:border-amber-400/50 focus:outline-none"
            />
            <Button onClick={addNote} disabled={!newName.trim() || !newContent.trim()} variant="secondary">
              Add note
            </Button>
          </div>
        </div>
      )}
    </Modal>
  );
}
