import { useEffect, useState } from "react";
import { Check, Pencil, Trash2, X as XIcon } from "lucide-react";
import { api } from "../lib/api";
import type { ReminderRow } from "../lib/types";
import { Button, ErrorNote, Modal } from "./ui";
import { cn } from "../lib/utils";

function ReminderItem({ reminder, onChanged }: { reminder: ReminderRow; onChanged: () => void }) {
  const [editing, setEditing] = useState(false);
  const [message, setMessage] = useState(reminder.message);
  const [fireAt, setFireAt] = useState(reminder.fire_at);
  const [recurrence, setRecurrence] = useState(reminder.recurrence ?? "");
  const [wakeMe, setWakeMe] = useState(!!reminder.wake_me);
  const [saving, setSaving] = useState(false);

  const save = async () => {
    setSaving(true);
    try {
      await api.editReminder(reminder.id, {
        message: message.trim(),
        fire_at: fireAt.trim(),
        recurrence: recurrence.trim() || null,
        wake_me: wakeMe,
      });
      setEditing(false);
      onChanged();
    } finally {
      setSaving(false);
    }
  };

  const del = async () => {
    if (!confirm("Delete this reminder?")) return;
    await api.deleteReminder(reminder.id);
    onChanged();
  };

  if (editing) {
    return (
      <div className="rounded-lg border border-slate-800 bg-slate-900/40 p-2.5">
        <input
          value={message}
          onChange={(e) => setMessage(e.target.value)}
          className="mb-1.5 w-full rounded-lg border border-slate-700 bg-slate-900/80 px-2 py-1 text-sm text-slate-200 focus:border-amber-400/50 focus:outline-none"
        />
        <input
          value={fireAt}
          onChange={(e) => setFireAt(e.target.value)}
          placeholder="Fire at (YYYY-MM-DD HH:MM)"
          className="mb-1.5 w-full rounded-lg border border-slate-700 bg-slate-900/80 px-2 py-1 text-xs text-slate-200 placeholder:text-slate-600 focus:border-amber-400/50 focus:outline-none"
        />
        <input
          value={recurrence}
          onChange={(e) => setRecurrence(e.target.value)}
          placeholder="Recurrence (optional, e.g. daily)"
          className="mb-1.5 w-full rounded-lg border border-slate-700 bg-slate-900/80 px-2 py-1 text-xs text-slate-200 placeholder:text-slate-600 focus:border-amber-400/50 focus:outline-none"
        />
        <label className="mb-2 flex items-center gap-1.5 text-xs text-slate-400">
          <input type="checkbox" checked={wakeMe} onChange={(e) => setWakeMe(e.target.checked)} />
          Wake me (override quiet hours)
        </label>
        <div className="flex gap-1.5">
          <Button onClick={save} loading={saving} disabled={saving} variant="secondary">
            <Check className="h-3.5 w-3.5" />
          </Button>
          <Button onClick={() => setEditing(false)} variant="ghost">
            <XIcon className="h-3.5 w-3.5" />
          </Button>
        </div>
      </div>
    );
  }

  return (
    <div className="flex items-center justify-between gap-2 rounded-lg border border-slate-800 bg-slate-900/40 px-2.5 py-1.5">
      <div className="min-w-0 flex-1">
        <div className={cn("truncate text-sm", reminder.fired ? "text-slate-500 line-through" : "text-slate-200")}>
          {reminder.message}
        </div>
        <div className="text-xs text-slate-500">
          {reminder.fire_at}
          {reminder.recurrence ? ` · ${reminder.recurrence}` : ""}
          {reminder.wake_me ? " · wakes you" : ""}
          {reminder.fired ? " · fired" : ""}
        </div>
      </div>
      <div className="flex shrink-0 items-center gap-1.5">
        <button onClick={() => setEditing(true)} className="text-slate-500 hover:text-amber-300" title="Edit">
          <Pencil className="h-3.5 w-3.5" />
        </button>
        <button onClick={del} className="text-slate-500 hover:text-rose-400" title="Delete">
          <Trash2 className="h-3.5 w-3.5" />
        </button>
      </div>
    </div>
  );
}

export function RemindersModal({ open, onClose }: { open: boolean; onClose: () => void }) {
  const [reminders, setReminders] = useState<ReminderRow[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [newMessage, setNewMessage] = useState("");
  const [newFireAt, setNewFireAt] = useState("");

  const refresh = () => {
    setLoading(true);
    api
      .getReminders()
      .then((r) => setReminders(r.reminders))
      .catch((e) => setError(e instanceof Error ? e.message : "Could not load reminders."))
      .finally(() => setLoading(false));
  };

  useEffect(() => {
    if (open) refresh();
  }, [open]);

  const addReminder = async () => {
    const msg = newMessage.trim();
    const fireAt = newFireAt.trim();
    if (!msg || !fireAt) return;
    await api.addReminder(msg, fireAt);
    setNewMessage("");
    setNewFireAt("");
    refresh();
  };

  return (
    <Modal open={open} onClose={onClose} title="Reminders" widthClassName="max-w-lg">
      {error && (
        <div className="mb-3">
          <ErrorNote>{error}</ErrorNote>
        </div>
      )}
      {loading ? (
        <p className="text-sm text-slate-500">Loading…</p>
      ) : (
        <div className="space-y-2">
          {reminders.length === 0 && <p className="text-sm text-slate-500">No reminders yet.</p>}
          {reminders.map((r) => (
            <ReminderItem key={r.id} reminder={r} onChanged={refresh} />
          ))}
          <div className="flex gap-1.5 pt-2">
            <input
              value={newMessage}
              onChange={(e) => setNewMessage(e.target.value)}
              placeholder="New reminder"
              className="flex-1 rounded-lg border border-slate-700 bg-slate-900/80 px-2.5 py-1.5 text-sm text-slate-200 placeholder:text-slate-600 focus:border-amber-400/50 focus:outline-none"
            />
            <input
              value={newFireAt}
              onChange={(e) => setNewFireAt(e.target.value)}
              placeholder="YYYY-MM-DD HH:MM"
              className="w-36 rounded-lg border border-slate-700 bg-slate-900/80 px-2.5 py-1.5 text-sm text-slate-200 placeholder:text-slate-600 focus:border-amber-400/50 focus:outline-none"
            />
            <Button onClick={addReminder} disabled={!newMessage.trim() || !newFireAt.trim()}>
              Add
            </Button>
          </div>
        </div>
      )}
    </Modal>
  );
}
