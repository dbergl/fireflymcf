// studio/web/src/glyphimport.ts
// Turning an imported image into `install_glyph`'s `rows`.
//
// A glyph carries COVERAGE, not colour: one value per pixel, painted later in whatever
// ink the widget's text colour resolves to. So the channel that matters in the source
// artwork is ALPHA -- the black disc behind a logo must not come along, only the marks.
// That is the same rule the original build script applied when it rasterised the Starlink
// SVG (`min(15, alpha >> 4)` per pixel), transcribed here so an imported PNG and the
// build script produce identical pixels from identical artwork.
//
// This module is deliberately DOM-free: it takes the RGBA bytes a canvas hands back, so
// the quantisation is unit-testable without a browser.

/** 4bpp coverage rows, one hex digit per pixel — exactly install_glyph's `rows`. */
export function rowsFromRGBA(rgba: Uint8ClampedArray, width: number,
                             height: number): string[] {
  if (rgba.length < width * height * 4) {
    throw new Error(`RGBA buffer holds ${rgba.length} bytes, need ${width * height * 4}`);
  }
  const rows: string[] = [];
  for (let y = 0; y < height; y++) {
    let row = "";
    for (let x = 0; x < width; x++) {
      const alpha = rgba[(y * width + x) * 4 + 3];
      row += Math.min(15, alpha >> 4).toString(16);
    }
    rows.push(row);
  }
  return rows;
}

/**
 * The y-bearing convention every shipped icon font in this corpus follows:
 * `(cell_height - glyph_height) / 2`, floored. The engine refuses anything else with
 * BEARING_OFF_CONVENTION unless `force_y` is set — a logo authored with y = 0 in a
 * 128-cell font drew 33 px above its button on real hardware (the v20 mistake), so the
 * importer computes it rather than letting anyone type it.
 */
export function conventionalBearing(cellHeight: number, glyphHeight: number): number {
  return Math.floor((cellHeight - glyphHeight) / 2);
}

/** Is every row the same length and made only of hex digits? (BAD_PARAMS, client-side.) */
export function rowsAreWellFormed(rows: string[]): boolean {
  if (rows.length === 0) return false;
  const w = rows[0].length;
  return w > 0 && rows.every((r) => r.length === w && /^[0-9a-f]+$/.test(r));
}

/**
 * A glyph exported from a build, as `cli glyph-export` / `GET /api/reference/glyph` emit it.
 * This is the COPY path, as distinct from the artwork path above: the pixels come from a
 * real record rather than a picture, and they arrive with two things no importer could
 * compute — the 10 ink-profile bytes the text measurer kerns against, and the flag bit in
 * record byte +0x0e. Neither is derivable from the bitmap (`GUICODE.md` Finding 46), so
 * they are carried through verbatim; drop them and the copy renders the same and *measures*
 * differently.
 */
export type ExportedGlyph = {
  slot: number;
  char: string;
  x: number;
  y: number;
  rows: string[];
  profiles: number[];
  flag: number;
};

const isByte = (v: unknown): v is number =>
  typeof v === "number" && Number.isInteger(v) && v >= 0 && v <= 255;

/**
 * Parse an exported glyph, accepting either the bare parameters or the `--as-op` wrapper.
 *
 * Validates client-side for the same reason the artwork path does: the engine's BAD_PARAMS
 * is correct but arrives only after a compile, and a file picked by hand is exactly where a
 * wrong file gets picked. Throws with the offending field named.
 */
export function parseExportedGlyph(text: string): ExportedGlyph {
  const doc = JSON.parse(text) as Record<string, unknown>;
  let spec = doc;
  if (doc && typeof doc === "object" && "params" in doc) {
    if (doc.type !== "install_glyph") {
      throw new Error(`this is a ${String(doc.type)} op, not an install_glyph glyph export`);
    }
    spec = doc.params as Record<string, unknown>;
  }
  const rows = spec.rows;
  if (!Array.isArray(rows) || !rows.every((r) => typeof r === "string")
      || !rowsAreWellFormed(rows as string[])) {
    throw new Error("rows must be equal-length strings of hex digits");
  }
  if (typeof spec.char !== "string" || spec.char.length !== 1) {
    throw new Error("char must be exactly one character");
  }
  const profiles = spec.profiles;
  if (!Array.isArray(profiles) || profiles.length !== 10 || !profiles.every(isByte)) {
    throw new Error("profiles must be 10 bytes in 0..255 (5 left, then 5 right)");
  }
  for (const field of ["slot", "x", "y", "flag"] as const) {
    if (typeof spec[field] !== "number" || !Number.isInteger(spec[field])) {
      throw new Error(`${field} must be an integer`);
    }
  }
  return {
    slot: spec.slot as number,
    char: spec.char,
    x: spec.x as number,
    y: spec.y as number,
    rows: rows as string[],
    profiles: profiles as number[],
    flag: spec.flag as number,
  };
}

/** A coarse ASCII preview, so an import can be eyeballed before it becomes an op. */
export function previewRows(rows: string[], maxWidth = 64): string[] {
  const ramp = " .:-=+*#%@";
  const step = Math.max(1, Math.ceil((rows[0]?.length ?? 0) / maxWidth));
  const out: string[] = [];
  for (let y = 0; y < rows.length; y += step * 2) {
    let line = "";
    for (let x = 0; x < rows[y].length; x += step) {
      const v = parseInt(rows[y][x], 16);
      line += ramp[Math.min(ramp.length - 1, Math.round((v / 15) * (ramp.length - 1)))];
    }
    out.push(line);
  }
  return out;
}
