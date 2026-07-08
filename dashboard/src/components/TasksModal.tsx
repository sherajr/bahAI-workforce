import { useEffect, useState } from "react";
import { Check, Pencil, Trash2, X as XIcon } from "lucide-react";
import { api } from "../lib/api";
import type { TaskRow } from "../lib/types";
import { Button, ErrorNote, Modal } from "./ui";
import { cn } from "../lib/utils";

function TaskItem({ task, onChanged }: { task: TaskRow; onChanged: () => void }) {
  const [editing, setEditing] = useState(false);
  const [description, setDescription] = useState(task.description);
  const [due, setDue] = useState(task.due ?? "");
  const [saving, setSaving] = useState(false);

  const toggleDone = async () => {
    await api.editTask(task.id, { done: !task.done });
    onChanged();
  };

  const save = async () => {
    setSaving(true);
    try {
      await api.editTask(task.id, { description: description.trim(), due: due.trim() || null });
      setEditing(false);
      onChanged();
    } finally {
      setSaving(false);
    }
  };

  const del = async () => {
    if (!confirm("Delete this task?")) return;
    await api.deleteTask(task.id);
    onChanged();
  };

  if (editing) {
    return (
      <div className="rounded-lg border border-slate-800 bg-slate-900/40 p-2.5">
        <input
          value={description}
          onChange={(e) => setDescription(e.target.value)}
          className="mb-1.5 w-full rounded-lg border border-slate-700 bg-slate-900/80 px-2 py-1 text-sm text-slate-200 focus:border-amber-400/50 focus:outline-none"
        />
        <input
          value={due}
          onChange={(e) => setDue(e.target.value)}
          placeholder="Due (optional, e.g. 2026-07-10)"
          className="mb-2 w-full rounded-lg border border-slate-700 bg-slate-900/80 px-2 py-1 text-xs text-slate-200 placeholder:text-slate-600 focus:border-amber-400/50 focus:outline-none"
        />
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
      <label className="flex min-w-0 flex-1 items-center gap-2">
        <input type="checkbox" checked={!!task.done} onChange={toggleDone} className="shrink-0" />
        <div className="min-w-0">
          <div className={cn("truncate text-sm", task.done ? "text-slate-500 line-through" : "text-slate-200")}>
            {task.description}
          </div>
          {task.due && <div className="text-xs text-slate-500">Due {task.due}</div>}
        </div>
      </label>
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

export function TasksModal({ open, onClose }: { open: boolean; onClose: () => void }) {
  const [tasks, setTasks] = useState<TaskRow[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [newDesc, setNewDesc] = useState("");
  const [newDue, setNewDue] = useState("");

  const refresh = () => {
    setLoading(true);
    api
      .getTasks()
      .then((r) => setTasks(r.tasks))
      .catch((e) => setError(e instanceof Error ? e.message : "Could not load tasks."))
      .finally(() => setLoading(false));
  };

  useEffect(() => {
    if (open) refresh();
  }, [open]);

  const addTask = async () => {
    const desc = newDesc.trim();
    if (!desc) return;
    await api.addTask(desc, newDue.trim() || undefined);
    setNewDesc("");
    setNewDue("");
    refresh();
  };

  return (
    <Modal open={open} onClose={onClose} title="Tasks" widthClassName="max-w-lg">
      {error && (
        <div className="mb-3">
          <ErrorNote>{error}</ErrorNote>
        </div>
      )}
      {loading ? (
        <p className="text-sm text-slate-500">Loading…</p>
      ) : (
        <div className="space-y-2">
          {tasks.length === 0 && <p className="text-sm text-slate-500">No tasks yet.</p>}
          {tasks.map((t) => (
            <TaskItem key={t.id} task={t} onChanged={refresh} />
          ))}
          <div className="flex gap-1.5 pt-2">
            <input
              value={newDesc}
              onChange={(e) => setNewDesc(e.target.value)}
              placeholder="New task"
              className="flex-1 rounded-lg border border-slate-700 bg-slate-900/80 px-2.5 py-1.5 text-sm text-slate-200 placeholder:text-slate-600 focus:border-amber-400/50 focus:outline-none"
            />
            <input
              value={newDue}
              onChange={(e) => setNewDue(e.target.value)}
              placeholder="Due (optional)"
              className="w-32 rounded-lg border border-slate-700 bg-slate-900/80 px-2.5 py-1.5 text-sm text-slate-200 placeholder:text-slate-600 focus:border-amber-400/50 focus:outline-none"
            />
            <Button onClick={addTask} disabled={!newDesc.trim()}>
              Add
            </Button>
          </div>
        </div>
      )}
    </Modal>
  );
}
