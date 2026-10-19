// studio/web/src/panes/ChangeList.tsx
// The project, visibly: every op in application order. Reorder by drag, toggle to
// disable without deleting, annotate (this is where a recorded judgement lives), and
// see a failed op's structured error with its offers as buttons.
import { useEffect, useState } from "react";
import type { CompileError, OpSpec } from "../api";

type Props = {
  ops: OpSpec[];
  errorsById: Record<string, CompileError>;
  onReorder: (from: number, to: number) => void;
  onToggle: (id: string) => void;
  onRemove: (id: string) => void;
  onNote: (id: string, note: string) => void;
  onAdd: () => void;
  onExport: () => void;
  onOffer?: (id: string, offer: string) => void;
  disabled?: boolean;
};

type NoteInputProps = { type: string; note: string; disabled?: boolean;
                        onCommit: (note: string) => void };

// FINDING 7: `note` is where recorded judgements live (an acknowledged warning, a
// trade-off rationale) -- it used to commit (save + recompile) on every keystroke,
// AND disable itself while that save was in flight, which yanked keyboard focus out
// from under the user mid-word. Local state decouples typing from saving; the commit
// point is blur, so a save fires once per edit, not once per character. Kept as its
// own component (module scope, not defined inside ChangeList) so React doesn't
// remount it -- and drop its local buffer -- on every unrelated parent re-render.
function NoteInput({ type, note, disabled, onCommit }: NoteInputProps) {
  const [value, setValue] = useState(note);
  // Re-sync when `note` changes for a reason OTHER than this input's own blur commit
  // -- e.g. applyOffer appending an acknowledgement note automatically.
  useEffect(() => { setValue(note); }, [note]);
  return (
    <input className="note" value={value} placeholder="note…" disabled={disabled}
           aria-label={`note for ${type}`}
           onChange={(e) => setValue(e.target.value)}
           onBlur={() => { if (value !== note) onCommit(value); }} />
  );
}

export default function ChangeList(p: Props) {
  return (
    <div className="changelist">
      <h3>Changes</h3>
      <ol>
        {p.ops.map((op, i) => {
          const err = p.errorsById[op.id];
          return (
            <li key={op.id} className={err ? "op failed" : "op"}
                draggable={!p.disabled}
                onDragStart={(e) => e.dataTransfer.setData("text/plain", String(i))}
                onDragOver={(e) => e.preventDefault()}
                onDrop={(e) => {
                  e.preventDefault();
                  // FINDING 4: drag-reorder was the one ChangeList control the
                  // existing `disabled` prop didn't actually gate -- checkboxes,
                  // remove and note all use the native `disabled` attribute, but a
                  // native browser drag ignores it. Check explicitly, on the drop
                  // that would otherwise call back into App mid-save.
                  if (p.disabled) return;
                  const from = Number(e.dataTransfer.getData("text/plain"));
                  if (!Number.isNaN(from) && from !== i) p.onReorder(from, i);
                }}>
              <div className="op-head">
                <input type="checkbox" checked={op.enabled} disabled={p.disabled}
                       aria-label={`enable ${op.type}`}
                       onChange={() => p.onToggle(op.id)} />
                <code>{op.type}</code>
                <button aria-label={`remove ${op.type}`} disabled={p.disabled}
                        onClick={() => p.onRemove(op.id)}>remove</button>
              </div>
              {err && (
                <div className="op-error">
                  <strong>{err.code}</strong>
                  <div>{err.message}</div>
                  {err.offers?.map((o) => (
                    <button key={o} disabled={p.disabled}
                            onClick={() => p.onOffer?.(op.id, o)}>{o}</button>
                  ))}
                </div>
              )}
              <NoteInput type={op.type} note={op.note} disabled={p.disabled}
                         onCommit={(n) => p.onNote(op.id, n)} />
            </li>
          );
        })}
      </ol>
      <div className="changelist-actions">
        <button onClick={p.onAdd}>+ Add</button>
        <button onClick={p.onExport}>Export →</button>
      </div>
    </div>
  );
}
