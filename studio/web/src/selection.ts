// selection.ts — grouping the three records of one control into one selection
// A control is three records (box, caption, touch) that share one obj_index. The
// canvas selects the whole object so the inspector can edit any of its parts --
// "Visual, touch and text are separate; moving one leaves the others behind"
// (MCF-FORMAT.md section 6), and the GUI should never make that mistake silently.
import type { Placement, PlacementsDoc } from "./api";

// `hit` is the record the click actually landed on; `placements` is every record of
// the object it belongs to. Both matter: a control is edited as a whole (its box,
// caption and touch region share an obj_index), but an object can also hold a hundred
// separate rows -- object 35 is the entire G12 OUTPUTS table -- and "the first label in
// the object" is then the wrong record to edit. Editors that act on one record should
// prefer `hit`; editors that act on the control as a whole use `placements`.
export type Selection = { obj_index: number; hit: Placement; placements: Placement[] };

const area = (b: [number, number, number, number]) =>
  Math.max(0, b[2] - b[0]) * Math.max(0, b[3] - b[1]);
const contains = (b: [number, number, number, number], x: number, y: number) =>
  x >= b[0] && x <= b[2] && y >= b[1] && y <= b[3];

// Does `p` beat the current `best` under the smallest-area rule? A degenerate 0x0 box
// (a container/attach record with no real extent) must never out-rank a real box just
// because 0 < any positive area -- it only wins when nothing better has matched yet
// (i.e. `best` is itself zero-area too, in which case the first one found keeps it).
function beats(p: Placement, best: Placement): boolean {
  const pa = area(p.box), ba = area(best.box);
  if (pa === 0 && ba > 0) return false;
  if (ba === 0 && pa > 0) return true;
  return pa < ba;
}

export function groupByObject(doc: PlacementsDoc, obj_index: number): Placement[] {
  return doc.placements.filter((p) => p.obj_index === obj_index);
}

export function selectAt(doc: PlacementsDoc, x: number, y: number): Selection | null {
  let best: Placement | null = null;
  for (const p of doc.placements) {
    if (!contains(p.box, x, y)) continue;
    if (!best || beats(p, best)) best = p;
  }
  if (!best) return null;
  return { obj_index: best.obj_index, hit: best,
           placements: groupByObject(doc, best.obj_index) };
}

/**
 * Re-point a selection at a freshly fetched placements document.
 *
 * Every applied op recompiles the image, so the boxes and raw geometry the inspector is
 * showing describe the image as it was BEFORE the edit. Without this the inspector kept
 * displaying stale values and built its next op's `expect` from them -- the engine
 * refuses that (EXPECT_MISMATCH, correctly), so the only way to make a second edit to
 * one control was to click it again. Re-selecting is not the user's job.
 *
 * The record is re-found by wtype and label rather than by position, because an op can
 * add or remove records within an object. Returns null when the object is no longer
 * placed at all (`remove_control` parks one off-canvas), which correctly clears the
 * inspector rather than leaving it pointed at something that is not on screen.
 */
export function refreshSelection(doc: PlacementsDoc,
                                  sel: Selection): Selection | null {
  const group = groupByObject(doc, sel.obj_index);
  if (group.length === 0) return null;
  const same = (a: Placement, b: Placement) =>
    a.wtype === b.wtype && a.label === b.label && a.kind === b.kind;
  const hit = (sel.hit && group.find((p) => same(p, sel.hit))) ?? group[0];
  return { obj_index: sel.obj_index, hit, placements: group };
}
