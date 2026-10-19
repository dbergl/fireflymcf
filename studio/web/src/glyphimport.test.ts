import { describe, expect, it } from "vitest";
import { conventionalBearing, parseExportedGlyph, previewRows, rowsAreWellFormed, rowsFromRGBA }
  from "./glyphimport";

const rgba = (pixels: [number, number, number, number][]) =>
  new Uint8ClampedArray(pixels.flat());

describe("rowsFromRGBA", () => {
  it("reads COVERAGE from alpha and ignores colour entirely", () => {
    // Two pixels of identical opacity but opposite colour must quantise the same: a
    // glyph is painted in the widget's ink, so the artwork's own colour never survives.
    const rows = rowsFromRGBA(rgba([[0, 0, 0, 255], [255, 255, 255, 255]]), 2, 1);
    expect(rows).toEqual(["ff"]);
  });

  it("quantises 8-bit alpha to 4bpp exactly as builds/from_stock.py does", () => {
    // min(15, alpha >> 4) — the transcription this whole module exists to keep honest.
    const alphas = [0, 15, 16, 17, 136, 239, 240, 255];
    const rows = rowsFromRGBA(rgba(alphas.map((a) => [0, 0, 0, a])), alphas.length, 1);
    expect(rows).toEqual(["00118eff"]);
  });

  it("splits rows by width, not by anything it guesses", () => {
    const px: [number, number, number, number][] = [];
    for (let i = 0; i < 6; i++) px.push([0, 0, 0, i * 17]);
    expect(rowsFromRGBA(rgba(px), 3, 2)).toEqual(["012", "345"]);
  });

  it("refuses a buffer too small for the stated size rather than reading past it", () => {
    expect(() => rowsFromRGBA(rgba([[0, 0, 0, 0]]), 4, 4)).toThrow(/need/);
  });
});

describe("conventionalBearing", () => {
  it("centres the glyph in the font cell — the reference build's logo is 37 in a 128-cell font", () => {
    expect(conventionalBearing(128, 54)).toBe(37);
  });
  it("floors, so an odd remainder cannot produce a fractional bearing", () => {
    expect(conventionalBearing(128, 55)).toBe(36);
  });
});

describe("rowsAreWellFormed", () => {
  it("accepts equal-length hex rows", () => {
    expect(rowsAreWellFormed(["00ff", "f00f"])).toBe(true);
  });
  it("rejects ragged rows, empty input and non-hex characters", () => {
    expect(rowsAreWellFormed(["00ff", "f0"])).toBe(false);
    expect(rowsAreWellFormed([])).toBe(false);
    expect(rowsAreWellFormed(["00gg"])).toBe(false);
  });
});

describe("previewRows", () => {
  it("renders coverage as an ASCII ramp", () => {
    const out = previewRows(["0f", "0f"]);
    expect(out[0][0]).toBe(" ");
    expect(out[0][1]).toBe("@");
  });
});

describe("parseExportedGlyph", () => {
  // A glyph exported by `cli glyph-export` (or GET /api/reference/glyph) carries more
  // than pixels: the 10 kerning bytes and the +0x0e flag bit are NOT derivable from the
  // bitmap (GUICODE.md Finding 46), so re-importing must pass them through untouched or
  // the reinstalled glyph kerns differently from the one it was copied from.
  const spec = {
    slot: 27, char: "A", x: 0, y: 3,
    rows: ["0f", "f0"],
    profiles: [0, 0, 0, 0, 0, 1, 1, 1, 1, 1],
    flag: 1,
  };

  it("accepts the bare export and keeps every field", () => {
    expect(parseExportedGlyph(JSON.stringify(spec))).toEqual(spec);
  });

  it("accepts the --as-op wrapper the CLI writes", () => {
    const wrapped = { id: "glyph-27-A", type: "install_glyph", params: spec };
    expect(parseExportedGlyph(JSON.stringify(wrapped))).toEqual(spec);
  });

  it("refuses a wrapper for some other op rather than silently taking its params", () => {
    const wrapped = { id: "x", type: "rename_label", params: spec };
    expect(() => parseExportedGlyph(JSON.stringify(wrapped))).toThrow(/install_glyph/);
  });

  it("refuses malformed rows", () => {
    expect(() => parseExportedGlyph(JSON.stringify({ ...spec, rows: ["0f", "f"] })))
      .toThrow(/rows/);
    expect(() => parseExportedGlyph(JSON.stringify({ ...spec, rows: ["zz"] })))
      .toThrow(/rows/);
  });

  it("refuses profiles that are not 10 bytes", () => {
    expect(() => parseExportedGlyph(JSON.stringify({ ...spec, profiles: [1, 2, 3] })))
      .toThrow(/profiles/);
    expect(() => parseExportedGlyph(JSON.stringify({ ...spec, profiles: Array(10).fill(300) })))
      .toThrow(/profiles/);
  });

  it("refuses a missing character, and one that is not a single character", () => {
    expect(() => parseExportedGlyph(JSON.stringify({ ...spec, char: "AB" }))).toThrow(/char/);
    const { char: _drop, ...noChar } = spec;
    expect(() => parseExportedGlyph(JSON.stringify(noChar))).toThrow(/char/);
  });

  it("refuses text that is not JSON at all", () => {
    expect(() => parseExportedGlyph("not json")).toThrow();
  });
});
