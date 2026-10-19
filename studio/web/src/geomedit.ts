// studio/web/src/geomedit.ts
// Building a `move_element` op from a geometry form.
//
// The canvas drag (App.tsx) already emits move_element, but only ever {x, y}: a drag
// can express a translation and nothing else. Resizing a control -- which is what
// "make this button 120x60" needs, and what the reference build's own l-geom/l-cap/
// l-touch ops do -- had no route through the UI at all until this module.
//
// Two rules the engine imposes, honoured here rather than discovered at compile:
//   * every `expect` key must also be a `set` key (an expect on a field that is never
//     written is a silently-inert assertion, and MoveElement.validate refuses it), so
//     `expect` is built from the changed keys only;
//   * a `set` key with no geometry field on the record is NO_FIELD unless it is listed
//     in `optional`. A record that carries a position but no size reports w/h as null
//     in its placement, so any such key goes into `optional` instead of being assumed.
import type { Placement } from "./api";
import type { OpDraft } from "./ops";

export type GeomKey = "x" | "y" | "w" | "h";
export const GEOM_KEYS: GeomKey[] = ["x", "y", "w", "h"];

/** Form state: one string per field, blank meaning "leave alone". */
export type GeomValues = Partial<Record<GeomKey, string>>;

export function geomOf(pl: Placement): Partial<Record<GeomKey, number | null>> {
  if (!pl.geom) return {};
  return { x: pl.geom.x, y: pl.geom.y, w: pl.geom.w, h: pl.geom.h };
}

/**
 * How to name this record inside its object. A caption is named by its own text --
 * which is both what a person reads and how the reference build's `battery` op selects the Chassis
 * label out of an object that carries two captions. Everything else is named by wtype
 * plus its index among same-wtype placements, exactly like the canvas drag.
 */
export function selectorFor(pl: Placement, siblings: Placement[]): Record<string, unknown> {
  if (pl.kind === "label" && pl.label) return { label: pl.label };
  const sameType = siblings.filter((s) => s.wtype === pl.wtype);
  const nth = Math.max(0, sameType.indexOf(pl));
  return { wtype: pl.wtype, nth };
}

/**
 * The op for this form state, or null when nothing actually changed -- so an untouched
 * geometry form never adds a change-list entry.
 */
export function moveElementDraft(
  obj: number, pl: Placement, siblings: Placement[], values: GeomValues,
): OpDraft | null {
  const current = geomOf(pl);
  const set: Record<string, number> = {};
  const expect: Record<string, number> = {};
  const optional: string[] = [];

  for (const key of GEOM_KEYS) {
    const raw = (values[key] ?? "").trim();
    if (raw === "") continue;
    const n = Number(raw);
    if (!Number.isInteger(n) || n < 0) continue;
    const was = current[key];
    if (was === n) continue;               // unchanged: not a write
    set[key] = n;
    if (was === null || was === undefined) optional.push(key);
    else expect[key] = was;
  }

  if (Object.keys(set).length === 0) return null;
  return {
    type: "move_element",
    params: {
      target: obj,
      selector: selectorFor(pl, siblings),
      set,
      ...(Object.keys(expect).length ? { expect } : {}),
      ...(optional.length ? { optional } : {}),
    },
  };
}

/**
 * Resize a WHOLE control in one gesture: one `move_element` per record that carries a
 * size, plus the box's own x when a position is given.
 *
 * A control is three independent records -- "Visual, touch and text are separate;
 * moving one leaves the others behind" (MCF-FORMAT.md §6) -- so making a button
 * 120x60 means three edits, and doing two of the three leaves a button whose touch
 * area no longer matches its paint. That is a firmware fact the owner should not have
 * to carry: the drafts below are exactly the ops reference build v1 writes by
 * hand as l-geom / l-cap / l-touch, produced from one width and one height.
 *
 * Records are visited in placement order, and only those that actually carry the field
 * are written -- a caption with no height field is skipped, not forced through with
 * `optional` (the engine would accept either; skipping keeps the op list honest about
 * what was edited).
 */
/**
 * A whole-control drag as one `move_attach`: the attach record's raw coordinates plus
 * the drag delta. `attach` is the server's per-object attach geometry (placements
 * `objects[..].attach`) -- null/absent means the page root carries no attach record
 * for this object, so there is nothing move_attach could write and the drag is a
 * no-op. Presence-masked like all record geometry: only the fields the record carries
 * are written. Results clamp at 0 -- the fields are u16, and a drag past the left/top
 * edge should pin to the edge, not wrap to ~65500.
 */
export function moveAttachDraft(
  page: string, obj: number, attach: { x?: number; y?: number } | null | undefined,
  dx: number, dy: number,
): OpDraft | null {
  if (!attach || (dx === 0 && dy === 0)) return null;
  const params: Record<string, unknown> = { page, child: obj };
  if (attach.x !== undefined && attach.x !== null) params.x = Math.max(0, attach.x + dx);
  if (attach.y !== undefined && attach.y !== null) params.y = Math.max(0, attach.y + dy);
  if (!("x" in params) && !("y" in params)) return null;
  return { type: "move_attach", params };
}

export function resizeControlDrafts(
  obj: number, placements: Placement[], size: { w?: number; h?: number; x?: number },
): OpDraft[] {
  const drafts: OpDraft[] = [];
  for (const pl of placements) {
    if (!pl.geom || !pl.editable.includes("move_element")) continue;
    const values: GeomValues = {};
    if (size.w !== undefined && pl.geom.w !== null) values.w = String(size.w);
    if (size.h !== undefined && pl.geom.h !== null) values.h = String(size.h);
    // x belongs to the control's own box only: writing it to the caption and the touch
    // region as well would move them relative to a box that has already moved.
    if (size.x !== undefined && pl.kind === "box" && pl.geom.x !== null) {
      values.x = String(size.x);
    }
    const draft = moveElementDraft(obj, pl, placements, values);
    if (draft) drafts.push(draft);
  }
  return drafts;
}
