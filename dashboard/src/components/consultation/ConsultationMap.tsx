import { useState } from "react";
import { BookOpen, ExternalLink, Pencil, Quote, X } from "lucide-react";
import type {
  ConsultationAction, ConsultationDecision, ConsultationStateMap, MapItem, VerifiedWriting,
  ActionStatus,
} from "../../lib/consultationTypes";
import { Button, Card, CardContent, CardHeader, CardTitle } from "../ui";

/**
 * The consultation as it stands, DURING the meeting.
 *
 * Four things and no more: where the group agrees, what is still unresolved,
 * what has been decided, and what happens next. That is what a person sitting
 * in a meeting can actually take in at a glance.
 *
 * It used to be ten collapsible lists — facts, assumptions, principles, ideas,
 * syntheses, questions to investigate and the rest — which is the reasoner's
 * working structure rendered straight onto the screen. Sheraj's verdict on
 * 2026-08-25 was "far too long to read", and he was right: the thing the model
 * thinks with and the thing a human reads are not the same object (rule 90).
 * The full map has not gone anywhere — it is in the Detail tab, and the report
 * at the end summarises it properly.
 *
 * Ideas here are the GROUP's — nothing is attributed to whoever said it
 * (rule 79) — and nothing is a decision until a person confirms one (rule 81).
 */

/**
 * One line of the map, correctable.
 *
 * Until 2026-09-03 this was inert text: whatever the model last wrote stayed on
 * screen for the rest of the meeting, mishearings included. The map is
 * Abigail's current understanding; the people in the room are the authority on
 * it (rule 95).
 */
function ItemRow({ item, list, onEdit, onDelete, onShowSource }: {
  item: MapItem;
  list: string;
  onEdit?: (list: string, id: string, text: string) => void;
  onDelete?: (list: string, id: string) => void;
  onShowSource?: (ids: string[]) => void;
}) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(item.text ?? "");
  const lifecycle = item.lifecycle ?? "open";
  const settled = lifecycle !== "open";
  const sources = item.source_turn_ids ?? [];

  const save = () => {
    const text = draft.trim();
    setEditing(false);
    if (text && text !== item.text) onEdit?.(list, item.id, text);
  };

  if (editing) {
    return (
      <li className="text-sm">
        <input
          autoFocus value={draft} onChange={(e) => setDraft(e.target.value)}
          onBlur={save}
          onKeyDown={(e) => {
            if (e.key === "Enter") save();
            if (e.key === "Escape") { setDraft(item.text ?? ""); setEditing(false); }
          }}
          className="w-full rounded border border-slate-700 bg-slate-950 px-2 py-1
                     text-sm text-slate-100 focus:border-amber-400/50 focus:outline-none"
        />
      </li>
    );
  }

  return (
    <li className="group/item flex items-start gap-1.5 text-sm leading-relaxed text-slate-300">
      <span className="mt-0.5 text-slate-600">·</span>
      <span className={`flex-1 ${settled ? "text-slate-500 line-through decoration-slate-700" : ""}`}>
        {item.text}
        {/* Nothing is deleted from the map any more (rule 97), so an item that
            has been dealt with is shown as dealt with rather than vanishing. */}
        {settled && (
          <span className="ml-1.5 text-xs not-italic text-slate-500">
            ({lifecycle.replace(/_/g, " ")}
            {item.resolution_note ? `: ${item.resolution_note}` : ""})
          </span>
        )}
        {item.human_edited && (
          <span className="ml-1.5 text-xs text-emerald-400/70" title="Corrected by hand">
            edited
          </span>
        )}
      </span>
      <span className="flex shrink-0 items-center gap-1 opacity-0 transition-opacity
                       group-hover/item:opacity-100">
        {sources.length > 0 && onShowSource && (
          <button onClick={() => onShowSource(sources)}
                  title="What was actually said"
                  className="text-slate-500 hover:text-amber-300">
            <Quote className="h-3 w-3" />
          </button>
        )}
        {onEdit && (
          <button onClick={() => { setDraft(item.text ?? ""); setEditing(true); }}
                  title="Correct this" className="text-slate-500 hover:text-slate-200">
            <Pencil className="h-3 w-3" />
          </button>
        )}
        {onDelete && (
          <button onClick={() => onDelete(list, item.id)}
                  title="She should not have written this down"
                  className="text-slate-500 hover:text-rose-400">
            <X className="h-3 w-3" />
          </button>
        )}
      </span>
    </li>
  );
}

/** A map item that remembers which list it belongs to. */
type ListedItem = MapItem & { list: string };

function ItemList({ items, empty, onEdit, onDelete, onShowSource }: {
  items: ListedItem[];
  empty: string;
  onEdit?: (list: string, id: string, text: string) => void;
  onDelete?: (list: string, id: string) => void;
  onShowSource?: (ids: string[]) => void;
}) {
  if (!items.length) {
    return <p className="text-sm text-slate-500">{empty}</p>;
  }
  return (
    <ul className="space-y-1.5">
      {items.map((item) => (
        <ItemRow key={`${item.list}:${item.id}`} item={item} list={item.list}
                 onEdit={onEdit} onDelete={onDelete} onShowSource={onShowSource} />
      ))}
    </ul>
  );
}

export function ConsultationMap({
  state, decisions, actions, writings, onConfirmDecision, onRejectDecision, onToggleAction,
  onEditItem, onDeleteItem, onShowSource, busy,
}: {
  state: ConsultationStateMap;
  decisions: ConsultationDecision[];
  actions: ConsultationAction[];
  writings: VerifiedWriting[];
  onConfirmDecision: (id: string) => void;
  onRejectDecision: (id: string) => void;
  onToggleAction: (id: string, status: ActionStatus) => void;
  /** Human authority over the map (rule 95). Optional so a read-only view --
   *  the finished-session Detail tab -- can render the same component. */
  onEditItem?: (list: string, id: string, text: string) => void;
  onDeleteItem?: (list: string, id: string) => void;
  onShowSource?: (ids: string[]) => void;
  busy?: boolean;
}) {
  const confirmed = decisions.find((d) => d.status === "confirmed");
  const candidates = decisions.filter((d) => d.status === "candidate");
  const agreements = (state.agreements ?? []) as MapItem[];
  // "Unresolved" is the honest union of the two things a person means by it:
  // where the group pulled apart, and what nobody has answered yet. Splitting
  // them into separate cards is a distinction that matters to the reasoner and
  // not to anyone in the room.
  // Each item carries which map list it came from, because editing one has to
  // address the right list on the server and this view deliberately merges two.
  const unresolved: ListedItem[] = [
    ...((state.tensions ?? []) as MapItem[]).map((i) => ({ ...i, list: "tensions" })),
    ...((state.unresolved_questions ?? []) as MapItem[])
      .map((i) => ({ ...i, list: "unresolved_questions" })),
  ];
  const agreementItems: ListedItem[] = agreements.map((i) => ({ ...i, list: "agreements" }));

  return (
    <div className="space-y-3">
      {state.summary && (
        <Card>
          <CardHeader><CardTitle>Where the consultation stands</CardTitle></CardHeader>
          <CardContent className="text-sm leading-relaxed text-slate-300">
            {state.summary}
          </CardContent>
        </Card>
      )}

      <div className="grid gap-3 md:grid-cols-2">
        <Card>
          <CardHeader><CardTitle>Where we agree</CardTitle></CardHeader>
          <CardContent>
            <ItemList items={agreementItems} empty="Nothing settled yet."
                      onEdit={onEditItem} onDelete={onDeleteItem}
                      onShowSource={onShowSource} />
          </CardContent>
        </Card>
        <Card>
          <CardHeader><CardTitle>Still unresolved</CardTitle></CardHeader>
          <CardContent>
            <ItemList items={unresolved} empty="Nothing outstanding."
                      onEdit={onEditItem} onDelete={onDeleteItem}
                      onShowSource={onShowSource} />
          </CardContent>
        </Card>
      </div>

      <Card className={confirmed ? "border-emerald-500/40" : undefined}>
        <CardHeader>
          <CardTitle>{confirmed ? "Decided" : "Nothing decided yet"}</CardTitle>
        </CardHeader>
        <CardContent className="space-y-2">
          {confirmed ? (
            <>
              <p className="text-sm leading-relaxed text-slate-200">{confirmed.text}</p>
              <Button variant="ghost" onClick={() => onRejectDecision(confirmed.id)}
                      disabled={busy} className="text-xs">
                Reopen this
              </Button>
            </>
          ) : (
            <p className="text-sm text-slate-500">
              A decision is only ever recorded when one of you confirms it.
            </p>
          )}
          {/* Candidates sit BELOW the decision, quietly. They used to be the
              loudest thing on the page, which turned a meeting into a queue of
              the assistant's guesses waiting to be adjudicated. */}
          {!confirmed && candidates.length > 0 && (
            <div className="space-y-2 border-t border-slate-800 pt-2">
              <p className="text-xs text-slate-500">
                This sounded like it might be a decision:
              </p>
              {candidates.map((c) => (
                <div key={c.id} className="space-y-1.5">
                  <p className="text-sm leading-relaxed text-slate-300">{c.text}</p>
                  <div className="flex gap-2">
                    <Button onClick={() => onConfirmDecision(c.id)} disabled={busy}
                            className="text-xs">
                      Confirm
                    </Button>
                    <Button variant="ghost" onClick={() => onRejectDecision(c.id)}
                            disabled={busy} className="text-xs">
                      Not a decision
                    </Button>
                  </div>
                </div>
              ))}
            </div>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader><CardTitle>What happens next</CardTitle></CardHeader>
        <CardContent className="space-y-2">
          {actions.length === 0 && (
            <p className="text-sm text-slate-500">No action items yet.</p>
          )}
          {actions.filter((a) => a.status !== "dropped").map((a) => (
            <label key={a.id} className="flex items-start gap-3 text-sm text-slate-300">
              <input
                type="checkbox"
                checked={a.status === "completed"}
                onChange={() =>
                  onToggleAction(a.id, a.status === "completed" ? "accepted" : "completed")}
                className="mt-1 accent-amber-400"
              />
              <span>
                {a.action}
                <span className="block text-xs text-slate-500">
                  {a.owner ?? "Owner not assigned"}
                  {/* Rule 95. A name is a PROPOSAL until somebody records that the
                      person accepted, and the three states are three different
                      facts. Reading a bare name as agreement is the exact mistake
                      this wording exists to stop. */}
                  {a.owner
                    ? a.owner_accepted === true
                      ? " · accepted"
                      : a.owner_accepted === false
                        ? " · did not accept"
                        : " · not yet accepted"
                    : ""}
                  {a.due ? ` · due ${a.due}` : ""}
                </span>
                {a.blocker ? (
                  <span className="block text-xs text-amber-300/80">Blocked: {a.blocker}</span>
                ) : null}
              </span>
            </label>
          ))}
        </CardContent>
      </Card>

      {writings.length > 0 && (
        <Card className="border-amber-400/30">
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <BookOpen className="h-4 w-4 text-amber-300" />
              Verified writings
            </CardTitle>
          </CardHeader>
          <CardContent className="space-y-3">
            <p className="text-xs text-amber-200/70">
              Exact text from the verified library. The assistant does not quote from memory.
            </p>
            {writings.map((w) => (
              <blockquote key={w.id} className="border-l-2 border-amber-400/40 pl-3">
                <p className="text-sm italic leading-relaxed text-slate-200">{w.text}</p>
                <footer className="mt-1 text-xs text-slate-500">
                  {[w.source, w.section].filter(Boolean).join(" — ")}
                  {w.link && (
                    <a href={w.link} target="_blank" rel="noreferrer"
                       className="ml-2 inline-flex items-center gap-1 text-amber-300 hover:text-amber-200">
                      source <ExternalLink className="h-3 w-3" />
                    </a>
                  )}
                </footer>
              </blockquote>
            ))}
          </CardContent>
        </Card>
      )}
    </div>
  );
}
