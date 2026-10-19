import { describe, expect, it } from "vitest";
import { moveAttachDraft, moveElementDraft, resizeControlDrafts,
         selectorFor } from "./geomedit";
import type { Placement } from "./api";

describe("moveAttachDraft", () => {
  it("adds the drag delta to the attach record's raw coordinates", () => {
    expect(moveAttachDraft("home", 10, { x: 5, y: 7 }, 20, -3)).toEqual({
      type: "move_attach",
      params: { page: "home", child: 10, x: 25, y: 4 },
    });
  });

  it("writes only the fields the attach record carries", () => {
    // Attach geometry is presence-masked like everything else: an x-only record
    // still takes an x move; the missing y is simply not written.
    expect(moveAttachDraft("home", 10, { x: 5 }, 20, 30)).toEqual({
      type: "move_attach",
      params: { page: "home", child: 10, x: 25 },
    });
  });

  it("is null when there is nothing to write", () => {
    expect(moveAttachDraft("home", 10, null, 20, 30)).toBeNull();
    expect(moveAttachDraft("home", 10, undefined, 20, 30)).toBeNull();
    expect(moveAttachDraft("home", 10, {}, 20, 30)).toBeNull();
    expect(moveAttachDraft("home", 10, { x: 5, y: 7 }, 0, 0)).toBeNull();
  });

  it("clamps a negative result to 0 rather than writing a wrapped u16", () => {
    expect(moveAttachDraft("home", 10, { x: 5, y: 7 }, -20, 0)).toEqual({
      type: "move_attach",
      params: { page: "home", child: 10, x: 0, y: 7 },
    });
  });
});

const pl = (over: Partial<Placement> = {}): Placement => ({
  obj_index: 7, wtype: 33, kind: "box", box: [0, 0, 10, 10], label: null,
  font: null, editable: ["move_element"], geom: { x: 135, y: 20, w: 84, h: 40 },
  ...over,
});

describe("moveElementDraft", () => {
  it("writes only what changed, and expects the prior value of each", () => {
    const d = moveElementDraft(7, pl(), [pl()],
                               { x: "39", y: "20", w: "120", h: "60" })!;
    expect(d.type).toBe("move_element");
    // y was typed but matches the current value, so it is not a write at all.
    expect(d.params.set).toEqual({ x: 39, w: 120, h: 60 });
    expect(d.params.expect).toEqual({ x: 135, w: 84, h: 40 });
    expect(d.params.optional).toBeUndefined();
  });

  it("returns null when nothing changed, so no change-list entry appears", () => {
    expect(moveElementDraft(7, pl(), [pl()], { x: "135", y: "20" })).toBeNull();
    expect(moveElementDraft(7, pl(), [pl()], {})).toBeNull();
  });

  it("marks a field the record does not carry as optional, never as an expect", () => {
    const noSize = pl({ geom: { x: 10, y: 20, w: null, h: null } });
    const d = moveElementDraft(7, noSize, [noSize], { w: "120", h: "60" })!;
    expect(d.params.set).toEqual({ w: 120, h: 60 });
    expect(d.params.optional).toEqual(["w", "h"]);
    // Every expect key must also be a set key; an absent field has no prior value to
    // assert, so it contributes no expect at all.
    expect(d.params.expect).toBeUndefined();
  });

  it("ignores non-integer and negative input rather than sending it to the engine", () => {
    expect(moveElementDraft(7, pl(), [pl()], { x: "abc", w: "-5", h: "1.5" })).toBeNull();
  });

  it("names a caption by its own text, so a two-caption object stays unambiguous", () => {
    const house = pl({ kind: "label", wtype: 11, label: "House" });
    const chassis = pl({ kind: "label", wtype: 11, label: "Chassis" });
    expect(selectorFor(chassis, [house, chassis])).toEqual({ label: "Chassis" });
  });

  it("names an unlabelled record by wtype and its index among its siblings", () => {
    const a = pl({ wtype: 12, kind: "touch" });
    const b = pl({ wtype: 12, kind: "touch" });
    expect(selectorFor(a, [a, b])).toEqual({ wtype: 12, nth: 0 });
    expect(selectorFor(b, [a, b])).toEqual({ wtype: 12, nth: 1 });
  });
});

describe("resizeControlDrafts", () => {
  const control = () => [
    pl({ kind: "box", wtype: 33, geom: { x: 135, y: null, w: 84, h: 50 } }),
    pl({ kind: "label", wtype: 11, label: "Overflow\nValve",
         geom: { x: null, y: null, w: 84, h: 50 },
         editable: ["rename_label", "set_font", "move_element"] }),
    pl({ kind: "touch", wtype: 12, geom: { x: null, y: null, w: 84, h: 50 },
         editable: ["set_gesture", "move_element"] }),
  ];

  it("writes every record of the control from one width and height", () => {
    const drafts = resizeControlDrafts(1422, control(), { w: 120, h: 60, x: 39 });
    expect(drafts).toHaveLength(3);
    // Exactly reference build v1's l-geom / l-cap / l-touch.
    expect(drafts[0].params.set).toEqual({ x: 39, w: 120, h: 60 });
    expect(drafts[1].params.set).toEqual({ w: 120, h: 60 });
    expect(drafts[2].params.set).toEqual({ w: 120, h: 60 });
  });

  it("puts x on the box alone — the caption and touch move with it", () => {
    const drafts = resizeControlDrafts(1422, control(), { x: 39 });
    expect(drafts).toHaveLength(1);
    expect(drafts[0].params.selector).toEqual({ wtype: 33, nth: 0 });
  });

  it("skips a record that does not carry the field, rather than forcing it", () => {
    const noHeight = [pl({ kind: "box", geom: { x: 1, y: 1, w: 84, h: null } })];
    const drafts = resizeControlDrafts(7, noHeight, { w: 120, h: 60 });
    expect(drafts[0].params.set).toEqual({ w: 120 });
    expect(drafts[0].params.optional).toBeUndefined();
  });

  it("produces nothing when the control is already that size", () => {
    expect(resizeControlDrafts(1422, control(), { w: 84, h: 50 })).toEqual([]);
  });
});
