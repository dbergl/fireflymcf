// studio/web/src/ExportDialog.tsx
// Compile → manifest → download. The manifest is how the owner learns what a build
// contains before flashing it, and `unverified` is how they learn which parts have
// never been proven on the coach.
//
// Once an export succeeds, the manifest/unverified panels freeze to the exported
// ExportResult rather than tracking the live `p.compile` -- nothing here blocks
// background editing while the dialog is open, so the live compile can keep changing
// after the download link is already handing out an older build. Showing the live
// manifest next to a frozen download would silently describe unexported work.
import { useState } from "react";
import * as api from "./api";
import type { CompileResult, ExportResult } from "./api";

type Props = { name: string; compile: CompileResult | undefined;
               buildName?: string | null; onClose: () => void };

export default function ExportDialog(p: Props) {
  const [result, setResult] = useState<ExportResult | null>(null);
  const [error, setError] = useState<api.ApiError | null>(null);
  const [busy, setBusy] = useState(false);
  const ok = p.compile?.ok === true;
  const manifest = result ? result.manifest : p.compile?.manifest;
  const unverified = result ? result.unverified : p.compile?.unverified;

  const run = async () => {
    setBusy(true); setError(null); setResult(null);
    try { setResult(await api.exportBuild(p.name)); }
    catch (e) {
      setError(e instanceof api.ApiError
        ? e : new api.ApiError(0, "UNKNOWN", { message: String(e) }));
    } finally { setBusy(false); }
  };

  return (
    <div className="card wide">
      <h3>Export</h3>

      {!ok && p.compile && (
        <div className="warn">
          This project does not compile. Fix these before exporting:
          <ul>
            {p.compile.errors.map((e, i) => (
              <li key={i}><strong>{e.code}</strong> — {e.message}</li>
            ))}
          </ul>
        </div>
      )}

      {!!unverified?.length && (
        <p className="warn">
          Contains hardware-unverified changes: {unverified.join(", ")}.
          Those mechanisms have never been flashed to a coach.
        </p>
      )}

      {/* The build name is what the coach's BOOT MENU lists; the filename below is
          what the stick holds. They are different strings and both matter when you
          are standing at the panel picking a build. */}
      <p className="muted">
        boot menu will show:{" "}
        {p.buildName
          ? <strong>{p.buildName}</strong>
          : <em>unset — keeps the stock name</em>}
      </p>

      <h4>Manifest</h4>
      {p.compile === undefined && !result ? (
        <p className="muted">compiling…</p>
      ) : (
        <ul className="manifest">
          {(manifest ?? []).map((m, i) => <li key={i}>{m}</li>)}
        </ul>
      )}

      {result && (
        <div className="result">
          <div><strong>{result.filename}</strong> · {result.size} bytes</div>
          <div className="muted mono">{result.sha256}</div>
          <a href={result.download} download={result.filename}>download</a>
        </div>
      )}

      {error && (
        <p className="warn"><strong>{error.code}</strong> {error.message}</p>
      )}

      <div className="card-actions">
        <button disabled={!ok || busy} onClick={run}>
          {busy ? "exporting…" : "export"}
        </button>
        <button onClick={p.onClose}>close</button>
      </div>
    </div>
  );
}
