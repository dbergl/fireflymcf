// studio/web/src/panes/ImportLive.tsx
//
// Import the coach's LIVE configuration from a candump capture.
//
// Optional, and structured so it stays that way. The input is TEXT -- paste it or pick a
// file -- so a capture can come from any bridge, adapter, or log someone forwarded, and
// Studio never needs to know which. Not every coach has the same bridge, and one that has
// none loses nothing: the control is collapsed by default and everything works without it.
//
// It PROPOSES. The server decodes and diffs but writes nothing; this shows the diff and
// applies only on an explicit click, because an import that silently rewrote the picker
// would change what you are looking at without saying so. Every failure -- a wrong log, a
// capture from another bus, a request that dies -- is a sentence and an unchanged picker.
import { useState } from "react";
import type { LiveImport, Preview } from "../api";

type Props = {
  onImport: (capture: string) => Promise<LiveImport>;
  onApply: (preview: Preview) => void;
};

const show = (v: unknown): string =>
  v === null || v === undefined ? "—"
    : Array.isArray(v) ? (v.length ? v.join(", ") : "—")
    : typeof v === "object" ? JSON.stringify(v)
    : String(v);

export default function ImportLive(p: Props) {
  const [capture, setCapture] = useState("");
  const [result, setResult] = useState<LiveImport | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const read = async () => {
    setBusy(true); setError(null); setResult(null);
    try {
      setResult(await p.onImport(capture));
    } catch (e) {
      setError(e instanceof Error ? e.message : "could not read that capture");
    } finally {
      setBusy(false);
    }
  };

  const clear = () => { setResult(null); setError(null); };

  return (
    <details className="import-live">
      <summary>Import from coach (optional)</summary>
      <p className="hint">
        Paste a <code>candump -L</code> capture, or choose a log file. Studio reads the
        G12's configuration-status broadcast; it never connects to anything itself.
      </p>

      <label>paste capture
        <textarea rows={3} value={capture}
                  onChange={(e) => { setCapture(e.target.value); clear(); }} />
      </label>

      <input type="file" aria-label="capture file"
             onChange={async (e) => {
               const f = e.target.files?.[0];
               if (f) { setCapture(await f.text()); clear(); }
             }} />

      <button onClick={read} disabled={busy || !capture.trim()}>
        {busy ? "Reading…" : "Read capture"}
      </button>

      {error && <p className="warn">{error}</p>}

      {result && (
        <>
          <p className="hint">
            {result.message}
            {result.frames > 0 &&
              ` (${result.frames} status frames${result.source
                 ? ` from ${result.source}` : ""})`}
          </p>
          {result.diff.length > 0 && (
            <>
              <table>
                <thead>
                  <tr><th>field</th><th>project</th><th>coach</th></tr>
                </thead>
                <tbody>
                  {result.diff.map((d) => (
                    <tr key={d.field}>
                      <td>{d.field}</td><td>{show(d.from)}</td><td>{show(d.to)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              <button onClick={() => { p.onApply(result.preview); clear(); }}>
                Apply to project
              </button>
              <button onClick={clear}>Cancel</button>
            </>
          )}
        </>
      )}
    </details>
  );
}
