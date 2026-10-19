// studio/web/src/panes/ImportGlyph.tsx
// The importer `install_glyph` has always needed.
//
// `install_glyph` takes `rows`: one hex digit of coverage per pixel, one string per row.
// That is data, not something anyone types, so the generated-form path correctly refused
// to collect it ("needs an importer for rows") and the op sat in the catalogue disabled
// -- the one the reference build feature the browser could not express at all.
//
// The rule for turning a picture into coverage is ALPHA, not luminance (a glyph is
// painted in the widget's own ink, so the artwork's colours never survive). It is the
// same rule `builds/from_stock.py` uses on the rasterised SVG, so the same artwork
// yields the same pixels here as in the build script -- verified: the Starlink SVG
// rendered to 96x54 imports to the exact rows of the glyph flashed to the coach.
import { useState } from "react";
import type { Font } from "../api";
import type { OpDraft } from "../ops";
import type { ExportedGlyph } from "../glyphimport";
import { conventionalBearing, parseExportedGlyph, previewRows, rowsAreWellFormed,
         rowsFromRGBA } from "../glyphimport";

type Props = {
  fonts: Font[];
  onCreate: (draft: OpDraft) => void;
  onCancel: () => void;
};

type Loaded = { rows: string[]; width: number; height: number; name: string };

export default function ImportGlyph(p: Props) {
  const [slot, setSlot] = useState<number | "">("");
  const [char, setChar] = useState("");
  const [loaded, setLoaded] = useState<Loaded | null>(null);
  // A COPIED glyph, from `cli glyph-export` / GET /api/reference/glyph. Its bearing, x,
  // kerning profiles and flag bit belong to the source record and are passed through --
  // recomputing the bearing would be wrong for a v-align-1 glyph (slot 27's Go Power!
  // icons are drawn that way), and the profiles cannot be recomputed at all.
  const [copied, setCopied] = useState<ExportedGlyph | null>(null);
  const [error, setError] = useState<string | null>(null);

  const font = p.fonts.find((f) => f.slot === slot);
  const bearing = copied
    ? copied.y
    : (loaded && font ? conventionalBearing(font.cell_height, loaded.height) : null);
  // The source glyph may sit off the convention legitimately, and it may be landing in a
  // font whose cell is a different height. Either way the engine would refuse the
  // unchanged bearing, so a copy says so explicitly rather than being silently re-centred.
  const offConvention = copied !== null && font !== undefined
    && copied.y !== conventionalBearing(font.cell_height, copied.rows.length);

  const read = async (file: File) => {
    setError(null);
    try {
      const bmp = await createImageBitmap(file);
      const canvas = document.createElement("canvas");
      canvas.width = bmp.width;
      canvas.height = bmp.height;
      const ctx = canvas.getContext("2d");
      if (!ctx) throw new Error("this browser gave no 2d canvas context");
      ctx.drawImage(bmp, 0, 0);
      // Only the alpha channel is read, so the 2d context's premultiplication (which
      // perturbs RGB on partially transparent pixels) cannot affect the result.
      const { data } = ctx.getImageData(0, 0, bmp.width, bmp.height);
      const rows = rowsFromRGBA(data, bmp.width, bmp.height);
      if (!rowsAreWellFormed(rows)) throw new Error("decoded to no usable pixels");
      setCopied(null);
      setLoaded({ rows, width: bmp.width, height: bmp.height, name: file.name });
    } catch (e) {
      setLoaded(null);
      setError(String((e as Error).message ?? e));
    }
  };

  const readExport = async (file: File) => {
    setError(null);
    try {
      const spec = parseExportedGlyph(await file.text());
      setLoaded({ rows: spec.rows, width: spec.rows[0].length, height: spec.rows.length,
                  name: file.name });
      setCopied(spec);
      setSlot(spec.slot);          // prefilled, and changeable: copying between fonts is
      setChar(spec.char);          // the point, as is landing on a different character
    } catch (e) {
      setLoaded(null);
      setCopied(null);
      setError(String((e as Error).message ?? e));
    }
  };

  const ready = loaded !== null && slot !== "" && char.length === 1 && bearing !== null;

  return (
    <div className="card wide">
      <h3>Import a glyph</h3>
      <p className="muted">
        A glyph is coverage, not colour: the image's <strong>alpha</strong> becomes the
        glyph and its colours are discarded — the panel paints it in the widget's own
        ink. Transparent PNG, sized as you want it drawn.
      </p>

      <label htmlFor="ig-file">Artwork</label>
      <input id="ig-file" type="file" accept="image/*" aria-label="glyph artwork"
             onChange={(e) => {
               const f = e.target.files?.[0];
               if (f) void read(f);
             }} />

      <label htmlFor="ig-export">…or copy a glyph that already exists</label>
      <input id="ig-export" type="file" accept="application/json,.json"
             aria-label="exported glyph"
             onChange={(e) => {
               const f = e.target.files?.[0];
               if (f) void readExport(f);
             }} />
      <p className="hint">
        A glyph exported from any build — <code>cli glyph-export</code>, or the panel's own{" "}
        <code>/api/reference/glyph</code>. It carries the kerning bytes and bearing that
        cannot be recovered from a picture, so the copy is byte-identical to its source.
      </p>
      {error && <p className="warn">{error}</p>}

      {loaded && (
        <>
          <p className="hint">
            {loaded.name} — {loaded.width}×{loaded.height} px
          </p>
          <pre className="glyph-preview" aria-label="glyph preview">
            {previewRows(loaded.rows).join("\n")}
          </pre>
        </>
      )}

      <label htmlFor="ig-slot">Font slot</label>
      <select id="ig-slot" value={slot}
              onChange={(e) => setSlot(e.target.value === "" ? "" : Number(e.target.value))}>
        <option value="" disabled>choose…</option>
        {p.fonts.map((f) => (
          <option key={f.slot} value={f.slot}>
            {`slot ${f.slot} · ${f.cell_height}px cell`}
          </option>
        ))}
      </select>

      <label htmlFor="ig-char">Character to claim</label>
      <input id="ig-char" value={char} maxLength={1}
             onChange={(e) => setChar(e.target.value)} />
      <p className="hint">
        The character whose slot points at this glyph. It must currently fall back to the
        font's placeholder — the engine refuses to overwrite a real one.
      </p>

      {/* The bearing is COMPUTED, never typed. A logo authored with y = 0 in a 128-cell
          font drew 33 px above its button on the coach; the engine refuses anything off
          the convention, so offering a free-text field here would only invite that
          mistake back. */}
      {bearing !== null && font && loaded && !copied && (
        <p className="hint">
          y-bearing {bearing} — centres a {loaded.height}px glyph in slot {font.slot}'s{" "}
          {font.cell_height}px cell.
        </p>
      )}
      {copied && font && (
        <p className={offConvention ? "warn" : "hint"}>
          y-bearing {copied.y}, kept from the source glyph
          {offConvention
            ? ` — that is not the ${conventionalBearing(font.cell_height,
                copied.rows.length)} this ${font.cell_height}px cell would centre at, so it
                is sent as a deliberate override. Right for a glyph its record draws
                top-aligned; wrong if you meant it centred.`
            : "."}
        </p>
      )}

      <div className="card-actions">
        <button disabled={!ready}
                onClick={() => p.onCreate({
                  type: "install_glyph",
                  params: copied
                    ? { slot: Number(slot), char, y: copied.y, rows: loaded!.rows,
                        x: copied.x, profiles: copied.profiles, flag: copied.flag,
                        ...(offConvention ? { force_y: true } : {}) }
                    : { slot: Number(slot), char, y: bearing as number,
                        rows: loaded!.rows },
                })}>add this change</button>
        <button onClick={p.onCancel}>cancel</button>
      </div>
    </div>
  );
}
