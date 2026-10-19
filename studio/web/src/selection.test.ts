import { describe, it, expect } from "vitest";
import * as sel from "./selection";
import type { PlacementsDoc } from "./api";

const P = (obj: number, kind: string, box: [number, number, number, number]) =>
  ({ obj_index: obj, wtype: 0, kind, box, label: null, font: null, editable: [],
     geom: null });

const doc: PlacementsDoc = {
  etag: "e", page: "home", root: 4, canvas: [800, 480],
  placements: [
    P(10, "box", [100, 100, 300, 200]),
    P(10, "label", [120, 130, 200, 160]),
    P(10, "touch", [100, 100, 300, 200]),
    P(20, "box", [400, 100, 500, 150]),
    // A small placement of a DIFFERENT object (41) nested inside a large placement of
    // object 40 -- unlike the obj-10 trio above, these two candidates at the shared
    // point do NOT share an obj_index, so this is the fixture that actually
    // discriminates "smallest wins" from "first wins" at the selection's own contract
    // (obj_index), not just at the record level. See the Step-6 can-fail note below.
    P(40, "box", [600, 150, 650, 200]),
    P(41, "box", [610, 160, 620, 170]),
    // A degenerate zero-area placement sitting exactly on a point that a real box also
    // covers -- must never out-rank the real box in the smallest-area tie-break just
    // because 0 < any positive area.
    P(70, "widget", [150, 140, 150, 140]),
  ] as any,
  objects: { "10": { binding: null }, "20": { binding: null }, "40": { binding: null },
            "41": { binding: null }, "70": { binding: null } },
};

describe("selectAt", () => {
  it("picks the smallest placement containing the point, then its whole object", () => {
    const s = sel.selectAt(doc, 150, 140)!;
    expect(s.obj_index).toBe(10);
    expect(s.placements).toHaveLength(3);       // box + label + touch grouped
  });

  it("selects the box when the point misses the caption", () => {
    const s = sel.selectAt(doc, 280, 190)!;
    expect(s.obj_index).toBe(10);
  });

  it("returns null outside every placement", () => {
    expect(sel.selectAt(doc, 700, 400)).toBeNull();
  });

  it("selects a different object", () => {
    expect(sel.selectAt(doc, 450, 120)!.obj_index).toBe(20);
  });

  it("picks the smaller of two overlapping DIFFERENT objects, not the first one listed", () => {
    // Object 40 (the large box) is listed BEFORE object 41 (the small, nested box) in
    // doc.placements, so this is the fixture the Step-6 can-fail check needs: "first
    // wins" and "smallest wins" disagree on obj_index here, not just on which record
    // of the same object gets returned.
    const s = sel.selectAt(doc, 615, 165)!;
    expect(s.obj_index).toBe(41);
  });

  it("a zero-area placement never out-ranks a real box in the tie-break", () => {
    // Object 70 is a degenerate 0x0 placement sitting exactly on (150, 140), the same
    // point object 10's real box/label/touch cover. 0 < any positive area, so a naive
    // "< " comparison would let it win by accident.
    const s = sel.selectAt(doc, 150, 140)!;
    expect(s.obj_index).toBe(10);
  });
});

describe("refreshSelection", () => {
  const doc = (labels: string[]): PlacementsDoc => ({
    etag: "e", page: "home", root: 4, canvas: [800, 480],
    objects: {},
    placements: labels.map((label, i) => ({
      obj_index: 10, wtype: 11, kind: "label",
      box: [0, i * 10, 50, i * 10 + 9] as [number, number, number, number],
      label, font: 13, editable: ["rename_label"],
      geom: { x: i, y: i, w: 50, h: 9 },
    })),
  });

  it("re-points at the same record, with its NEW geometry", () => {
    const before = doc(["A", "B"]);
    const selection = { obj_index: 10, hit: before.placements[1],
                  placements: before.placements };
    const after = doc(["A", "B"]);
    after.placements[1] = { ...after.placements[1],
                            geom: { x: 99, y: 99, w: 120, h: 60 } };
    const out = sel.refreshSelection(after, selection)!;
    expect(out.hit.label).toBe("B");
    expect(out.hit.geom).toEqual({ x: 99, y: 99, w: 120, h: 60 });
  });

  it("clears the selection when the object is no longer placed", () => {
    const selection = { obj_index: 10, hit: doc(["A"]).placements[0],
                  placements: doc(["A"]).placements };
    const empty: PlacementsDoc = { ...doc([]), placements: [] };
    expect(sel.refreshSelection(empty, selection)).toBeNull();
  });

  it("falls back to the object's first record when the exact one is gone", () => {
    const selection = { obj_index: 10, hit: doc(["A", "GONE"]).placements[1],
                  placements: doc(["A", "GONE"]).placements };
    const out = sel.refreshSelection(doc(["A"]), selection)!;
    expect(out.hit.label).toBe("A");
  });
});
