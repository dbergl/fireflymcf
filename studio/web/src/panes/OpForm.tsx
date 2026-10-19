// studio/web/src/panes/OpForm.tsx
// A form generated from an op's `/api/schemas` entry — the fallback for ops with no
// hand-written control (see opforms.ts for why that distinction exists and why the
// curated controls are not generated).
//
// It collects values and lets the ENGINE judge them. The only checking here is
// structural and comes from the schema itself — which fields exist, which are
// required, what each one's declared type is. Anything beyond that (is this pin
// already commanded, does this label still fit, is this glyph bearing centred) is
// knowledge the engine holds and answers with a structured error the change list
// renders. The client does not re-derive it.
import { useState } from "react";
import type { OpSchema } from "../api";
import type { OpDraft } from "../ops";
import { fieldsOf, initialValues, isComplete, paramsFrom } from "../opforms";
import type { Values } from "../opforms";

type Props = {
  type: string;
  schema: OpSchema;
  onCreate: (draft: OpDraft) => void;
  onCancel: () => void;
};

export default function OpForm(p: Props) {
  const fields = fieldsOf(p.schema);
  const [values, setValues] = useState<Values>(() => initialValues(fields));
  const set = (name: string, v: string | boolean) =>
    setValues((prev) => ({ ...prev, [name]: v }));

  return (
    <div className="card wide">
      <h3><code>{p.type}</code></h3>
      <p className="muted">{p.schema.doc}</p>

      {p.schema.hardware_verified === false && (
        <p className="warn">
          This mechanism has never been flashed to a coach. Every build containing it
          is flagged hardware-unverified in its manifest.
        </p>
      )}

      {fields.length === 0 && (
        <p className="muted">This change takes no settings.</p>
      )}

      {fields.map((f) => (
        <div key={f.name} className="opfield">
          <label htmlFor={`opf-${f.name}`}>
            {f.name}{f.required ? " *" : ""}
          </label>
          {f.kind === "boolean" ? (
            <input id={`opf-${f.name}`} type="checkbox"
                   checked={values[f.name] === true}
                   onChange={(e) => set(f.name, e.target.checked)} />
          ) : f.kind === "enum" ? (
            <select id={`opf-${f.name}`} value={String(values[f.name] ?? "")}
                    onChange={(e) => set(f.name, e.target.value)}>
              <option value="">choose…</option>
              {(f.options ?? []).map((o) => (
                <option key={o} value={o}>{o}</option>
              ))}
            </select>
          ) : (
            <input id={`opf-${f.name}`}
                   inputMode={f.kind === "integer" ? "numeric" : undefined}
                   placeholder={f.kind === "rgb" ? "r,g,b" : undefined}
                   value={String(values[f.name] ?? "")}
                   onChange={(e) => set(f.name, e.target.value)} />
          )}
          {f.description && <p className="hint">{f.description}</p>}
        </div>
      ))}

      <p className="muted">
        These fields come straight from the engine's own definition. If a value is
        wrong for this image, the engine refuses it and the change list shows why.
      </p>

      <div className="card-actions">
        <button disabled={!isComplete(fields, values)}
                onClick={() => p.onCreate({
                  type: p.type, params: paramsFrom(fields, values),
                })}>add this change</button>
        <button onClick={p.onCancel}>cancel</button>
      </div>
    </div>
  );
}
