// studio/web/src/ops.test.ts
import { describe, it, expect } from "vitest";
import * as ops from "./ops";
import type { OpSpec } from "./api";

const op = (id: string, type: string, params: any, enabled = true): OpSpec =>
  ({ id, type, params, enabled, note: "" });

describe("upsertOp", () => {
  it("replaces the op already targeting the same field instead of appending", () => {
    const list = [op("a", "rename_label", { object: 35, old: "28 - ", new: "X" })];
    const out = ops.upsertOp(list, { type: "rename_label",
      params: { object: 35, old: "28 - ", new: "Y" } });
    expect(out).toHaveLength(1);
    expect(out[0].id).toBe("a");
    expect(out[0].params.new).toBe("Y");
  });

  it("appends when the target differs", () => {
    const list = [op("a", "rename_label", { object: 35, old: "28 - ", new: "X" })];
    const out = ops.upsertOp(list, { type: "rename_label",
      params: { object: 4, old: "   HOME", new: "Z" } });
    expect(out).toHaveLength(2);
    expect(out[1].id).not.toBe("a");
  });

  it("appends when the type differs on the same object", () => {
    const list = [op("a", "recolor", { object: 279, color: { index: 3 } })];
    const out = ops.upsertOp(list, { type: "move_element",
      params: { target: 279, selector: { wtype: 33 }, set: { x: 10 } } });
    expect(out).toHaveLength(2);
  });

  it("distinguishes move_element ops by selector on one object", () => {
    const list = [op("a", "move_element",
      { target: 902, selector: { label: "Chassis" }, set: { y: 6 } })];
    const out = ops.upsertOp(list, { type: "move_element",
      params: { target: 902, selector: { label: "House" }, set: { y: 8 } } });
    expect(out).toHaveLength(2);
  });

  it("treats a {ref} target as its own identity", () => {
    const list = [op("a", "set_gesture",
      { target: { ref: "btn" }, kind: "hold_to_toggle", seconds: 3 })];
    const out = ops.upsertOp(list, { type: "set_gesture",
      params: { target: { ref: "btn" }, kind: "latch" } });
    expect(out).toHaveLength(1);
    expect(out[0].params.kind).toBe("latch");
  });

  it("distinguishes set_gesture ops by group when present", () => {
    const list = [op("a", "set_gesture",
      { target: -1, group: 12, kind: "hold_to_toggle", seconds: 3 })];
    const out = ops.upsertOp(list, { type: "set_gesture",
      params: { target: -1, group: 94, kind: "hold_to_toggle", seconds: 3 } });
    expect(out).toHaveLength(2);
  });

  it("updates set_gesture on same group", () => {
    const list = [op("a", "set_gesture",
      { target: -1, group: 12, kind: "hold_to_toggle", seconds: 3 })];
    const out = ops.upsertOp(list, { type: "set_gesture",
      params: { target: -1, group: 12, kind: "latch" } });
    expect(out).toHaveLength(1);
    expect(out[0].id).toBe("a");
    expect(out[0].params.kind).toBe("latch");
  });

  it("preserves an existing note when replacing, and applies a supplied one", () => {
    const list = [{ ...op("a", "recolor", { object: 279, color: { index: 3 } }),
                    note: "keep me" }];
    const same = ops.upsertOp(list, { type: "recolor",
      params: { object: 279, color: { index: 4 } } });
    expect(same[0].note).toBe("keep me");
    const noted = ops.upsertOp(list, { type: "recolor",
      params: { object: 279, color: { index: 5 } }, note: "acknowledged" });
    expect(noted[0].note).toBe("acknowledged");
  });

  it("keeps ops with no target distinct by type only", () => {
    const list = [op("a", "tank_heater_names", {})];
    const out = ops.upsertOp(list, { type: "tank_heater_names", params: {} });
    expect(out).toHaveLength(1);
  });

  it("distinguishes add_button ops by channel", () => {
    const list = [op("a", "add_button", { channel: 28, label: "Valve", off_colour: [0, 0, 0] })];
    const out = ops.upsertOp(list, { type: "add_button",
      params: { channel: 29, label: "Starlink", off_colour: [0, 0, 0] } });
    expect(out).toHaveLength(2);
  });

  it("updates add_button on same channel", () => {
    const list = [op("a", "add_button", { channel: 28, label: "Valve", off_colour: [0, 0, 0] })];
    const out = ops.upsertOp(list, { type: "add_button",
      params: { channel: 28, label: "Valve Updated", off_colour: [255, 0, 0] } });
    expect(out).toHaveLength(1);
    expect(out[0].id).toBe("a");
    expect(out[0].params.label).toBe("Valve Updated");
  });

  it("distinguishes remap_channel ops by label", () => {
    const list = [op("a", "remap_channel", { label: "Bath Fan", channel: 40 })];
    const out = ops.upsertOp(list, { type: "remap_channel",
      params: { label: "Starlink", channel: 39 } });
    expect(out).toHaveLength(2);
  });

  it("updates remap_channel on same label", () => {
    const list = [op("a", "remap_channel", { label: "Bath Fan", channel: 40 })];
    const out = ops.upsertOp(list, { type: "remap_channel",
      params: { label: "Bath Fan", channel: 41 } });
    expect(out).toHaveLength(1);
    expect(out[0].id).toBe("a");
    expect(out[0].params.channel).toBe(41);
  });

  it("distinguishes install_glyph ops by slot AND char", () => {
    const list = [op("a", "install_glyph", { slot: 27, char: "E", y: 0, rows: [] })];
    const out1 = ops.upsertOp(list, { type: "install_glyph",
      params: { slot: 27, char: "F", y: 0, rows: [] } });
    expect(out1).toHaveLength(2);
    const out2 = ops.upsertOp(list, { type: "install_glyph",
      params: { slot: 28, char: "E", y: 0, rows: [] } });
    expect(out2).toHaveLength(2);
  });

  it("updates install_glyph on same slot AND char", () => {
    const list = [op("a", "install_glyph", { slot: 27, char: "E", y: 0, rows: [] })];
    const out = ops.upsertOp(list, { type: "install_glyph",
      params: { slot: 27, char: "E", y: 33, rows: ["ff", "ff"] } });
    expect(out).toHaveLength(1);
    expect(out[0].id).toBe("a");
    expect(out[0].params.y).toBe(33);
  });

  it("always appends add_glyph_button (never collapses)", () => {
    const params = { page: "home", x: 100, y: 100, w: 50, h: 50, fill_cell: 0 };
    const list = [op("a", "add_glyph_button", params)];
    const out = ops.upsertOp(list, { type: "add_glyph_button", params });
    expect(out).toHaveLength(2);
    expect(out[0].id).toBe("a");
    expect(out[1].id).not.toBe("a");
  });

  it("handles selector key order stability (sorted keys)", () => {
    const list = [op("a", "move_element",
      { target: 902, selector: { wtype: 33, label: "Chassis" }, set: { y: 6 } })];
    // Same selector with keys in different order should collapse
    const out = ops.upsertOp(list, { type: "move_element",
      params: { target: 902, selector: { label: "Chassis", wtype: 33 }, set: { y: 8 } } });
    expect(out).toHaveLength(1);
    expect((out[0].params.set as { y: number }).y).toBe(8);
  });
});

// FINDING 1: an op carrying a PRE-IMAGE (move_element's `expect`, rename_label's
// `old`) goes stale under the upsert rule. upsertOp replaces `params` wholesale on an
// update, but the client builds a fresh draft from the CURRENTLY COMPILED image on
// every edit -- so a second edit's pre-image describes the post-first-edit state, not
// stock, and the engine (which always applies the single collapsed op against stock)
// correctly refuses it. `preserve` keeps the named keys from the EXISTING op instead.
describe("upsertOp — preserve", () => {
  it("keeps the existing op's named key on update, ignoring the draft's own value", () => {
    const list = [op("a", "move_element",
      { target: 10, selector: { wtype: 11, nth: 0 }, set: { x: 40, y: 17 },
        expect: { x: 30, y: 7 } })];
    // A second drag's draft recomputes `expect` off the (already-moved) placement --
    // that value must be discarded in favour of what op "a" already recorded.
    const out = ops.upsertOp(list, { type: "move_element",
      params: { target: 10, selector: { wtype: 11, nth: 0 }, set: { x: 55, y: 30 },
                expect: { x: 40, y: 17 } } }, ["expect"]);
    expect(out).toHaveLength(1);
    expect(out[0].params.set).toEqual({ x: 55, y: 30 });     // the new value DOES apply
    expect(out[0].params.expect).toEqual({ x: 30, y: 7 });   // the pre-image does not
  });

  it("has no effect on an APPENDED (new) op -- there is no existing op to preserve from", () => {
    const out = ops.upsertOp([], { type: "move_element",
      params: { target: 10, selector: { wtype: 11, nth: 0 }, set: { x: 40, y: 17 },
                expect: { x: 30, y: 7 } } }, ["expect"]);
    expect(out).toHaveLength(1);
    expect(out[0].params.expect).toEqual({ x: 30, y: 7 });
  });

  it("preserves rename_label's `old` across a second rename", () => {
    const list = [op("a", "rename_label",
      { object: 35, old: "28 - ", new: "28 - Valve" })];
    // A second rename reads `old` off the label AFTER the first rename compiled --
    // "28 - Valve" -- but the engine needs the ORIGINAL stock text. Two things now
    // carry that: `withResolvedPreImage` finds the row this edit belongs to (identity
    // includes the row's stock text, so two rows of one object stay two changes), and
    // `preserve` keeps the stored pre-image over the draft's.
    const out = ops.upsertOp(list, ops.withResolvedPreImage(list, { type: "rename_label",
      params: { object: 35, old: "28 - Valve", new: "28 - Overflow Valve" } }),
      ["old"]);
    expect(out).toHaveLength(1);
    expect(out[0].params.new).toBe("28 - Overflow Valve");   // the new value DOES apply
    expect(out[0].params.old).toBe("28 - ");                 // the pre-image does not
  });

  it("a key named in preserve that the draft never set falls back to the draft's own params", () => {
    // Guards against preserve silently requiring every key to already exist -- if the
    // EXISTING op never had the key either, applyPreserve must not invent `undefined`.
    const list = [op("a", "move_element",
      { target: 10, set: { x: 1 } })];   // no `expect` on the existing op
    const out = ops.upsertOp(list, { type: "move_element",
      params: { target: 10, set: { x: 2 }, expect: { x: 1 } } }, ["expect"]);
    expect(out[0].params.expect).toEqual({ x: 1 });
  });

  // Behavioural: two drags collapsing into one op, entirely in terms of the pure
  // upsert/preserve mechanism (App.test.tsx exercises the same scenario end to end
  // through the real component). The `expect` the SECOND drag's draft carries is
  // necessarily stale (it reads the already-moved placement) -- preserve is what keeps
  // the collapsed op's `expect` naming the geometry stock actually has.
  it("two successive drags collapse into one op whose expect still names stock", () => {
    let list: OpSpec[] = [];
    // Drag 1: stock is at (30, 7); drag by (+10, +10).
    list = ops.upsertOp(list, { type: "move_element",
      params: { target: 10, selector: { wtype: 11, nth: 0 }, set: { x: 40, y: 17 },
                expect: { x: 30, y: 7 } } }, ["expect"]);
    // Drag 2: the client now sees the COMPILED placement at (40, 17) and drags by
    // (+15, +13) from there -- its own draft's `expect` reflects that, not stock.
    list = ops.upsertOp(list, { type: "move_element",
      params: { target: 10, selector: { wtype: 11, nth: 0 }, set: { x: 55, y: 30 },
                expect: { x: 40, y: 17 } } }, ["expect"]);
    expect(list).toHaveLength(1);
    expect(list[0].params.set).toEqual({ x: 55, y: 30 });
    expect(list[0].params.expect).toEqual({ x: 30, y: 7 });   // STOCK, not (40, 17)
  });

  it("two successive renames collapse into one op whose old still names stock's text", () => {
    let list: OpSpec[] = [];
    list = ops.upsertOp(list, { type: "rename_label",
      params: { object: 35, old: "28 - ", new: "28 - Valve" } }, ["old"]);
    // The second rename's draft reads `old` off the label as it now reads post-first-
    // rename: "28 - Valve" -- not stock's "28 - ". Resolving that back to stock is now
    // part of collapsing it, because identity includes the ROW's pre-image (renaming a
    // second row of the same object must not replace the first). `preserve` alone was
    // enough when a rename's identity was the object; it no longer is.
    list = ops.upsertOp(list, ops.withResolvedPreImage(list, { type: "rename_label",
      params: { object: 35, old: "28 - Valve", new: "28 - Overflow Valve" } }), ["old"]);
    expect(list).toHaveLength(1);
    expect(list[0].params.new).toBe("28 - Overflow Valve");
    expect(list[0].params.old).toBe("28 - ");   // STOCK, not "28 - Valve"
  });
});

describe("upsertOpWithId", () => {
  it("returns the appended id on a new op", () => {
    const { ops: out, id } = ops.upsertOpWithId([], { type: "add_button",
      params: { channel: 31, label: "Valve", off_colour: [0, 0, 0] } });
    expect(out).toHaveLength(1);
    expect(out[0].id).toBe(id);
  });

  it("returns the EXISTING id when the draft updates rather than appends", () => {
    const list = [op("op1", "add_button",
      { channel: 31, label: "Old", off_colour: [0, 0, 0] })];
    const { ops: out, id } = ops.upsertOpWithId(list, { type: "add_button",
      params: { channel: 31, label: "New", off_colour: [1, 1, 1] } });
    expect(out).toHaveLength(1);
    expect(id).toBe("op1");
    expect(out[0].params.label).toBe("New");
  });
});

describe("applyOpDrafts", () => {
  it("substitutes PENDING_TARGET with the freshly-appended op's id", () => {
    const drafts = [
      { type: "add_button",
        params: { channel: 31, label: "Starlink", off_colour: [0, 0, 0] } },
      { type: "set_gesture",
        params: { target: ops.PENDING_TARGET, kind: "hold_to_toggle", seconds: 3 } },
    ];
    const out = ops.applyOpDrafts([], drafts);
    expect(out).toHaveLength(2);
    const button = out.find((o) => o.type === "add_button")!;
    const gesture = out.find((o) => o.type === "set_gesture")!;
    expect(gesture.params.target).toEqual({ ref: button.id });
  });

  // The critical case: re-opening "Add a button" on a pin that already has an
  // add_button op makes upsertOpWithId UPDATE it (same channel identity), not
  // append -- so the id the gesture must reference is an EXISTING id, and a
  // before/after-diff approach would find no new id at all here.
  it("substitutes with the EXISTING op's id when add_button updates, and never "
     + "leaves the sentinel in a saved op", () => {
    const existing = [op("op1", "add_button",
      { channel: 31, label: "Old", off_colour: [0, 0, 0] })];
    const drafts = [
      { type: "add_button",
        params: { channel: 31, label: "New", off_colour: [1, 1, 1] } },
      { type: "set_gesture",
        params: { target: ops.PENDING_TARGET, kind: "hold_to_toggle", seconds: 3 } },
    ];
    const out = ops.applyOpDrafts(existing, drafts);
    expect(out).toHaveLength(2);
    const gesture = out.find((o) => o.type === "set_gesture")!;
    expect(gesture.params.target).toEqual({ ref: "op1" });
    expect(JSON.stringify(out)).not.toContain("__pending_add_button__");
  });

  it("passes params through unchanged when there is no PENDING_TARGET to resolve", () => {
    const drafts = [{ type: "add_button",
      params: { channel: 31, label: "Valve", off_colour: [0, 0, 0] } }];
    const out = ops.applyOpDrafts([], drafts);
    expect(out).toHaveLength(1);
    expect(out[0].params.label).toBe("Valve");
  });

  // Same corruption class as the update case above, different trigger: nothing
  // enforces that add_button leads a batch, so a caller (present or future) that
  // hands applyOpDrafts a set_gesture-first batch must not have the sentinel pass
  // through silently -- it must refuse loudly instead of persisting garbage.
  it("throws rather than persisting the sentinel when the FIRST draft carries "
     + "PENDING_TARGET (no preceding op to resolve it)", () => {
    const drafts = [
      { type: "set_gesture",
        params: { target: ops.PENDING_TARGET, kind: "hold_to_toggle", seconds: 3 } },
    ];
    expect(() => ops.applyOpDrafts([], drafts)).toThrow(/PENDING_TARGET/);
  });

  it("still succeeds for the ordinary add_button-then-set_gesture batches", () => {
    const fresh = ops.applyOpDrafts([], [
      { type: "add_button",
        params: { channel: 31, label: "Starlink", off_colour: [0, 0, 0] } },
      { type: "set_gesture",
        params: { target: ops.PENDING_TARGET, kind: "hold_to_toggle", seconds: 3 } },
    ]);
    expect(fresh).toHaveLength(2);

    const existing = [op("op1", "add_button",
      { channel: 31, label: "Old", off_colour: [0, 0, 0] })];
    const updated = ops.applyOpDrafts(existing, [
      { type: "add_button",
        params: { channel: 31, label: "New", off_colour: [1, 1, 1] } },
      { type: "set_gesture",
        params: { target: ops.PENDING_TARGET, kind: "hold_to_toggle", seconds: 3 } },
    ]);
    expect(updated).toHaveLength(2);
  });
});

describe("list mutations", () => {
  const list = [op("a", "x", {}), op("b", "y", {}), op("c", "z", {})];

  it("reorders without mutating the input", () => {
    const out = ops.reorderOp(list, 0, 2);
    expect(out.map((o) => o.id)).toEqual(["b", "c", "a"]);
    expect(list.map((o) => o.id)).toEqual(["a", "b", "c"]);
  });

  it("toggles enabled", () => {
    expect(ops.toggleOp(list, "b")[1].enabled).toBe(false);
    expect(ops.toggleOp(ops.toggleOp(list, "b"), "b")[1].enabled).toBe(true);
  });

  it("removes by id and sets notes", () => {
    expect(ops.removeOp(list, "b").map((o) => o.id)).toEqual(["a", "c"]);
    expect(ops.setNote(list, "c", "why")[2].note).toBe("why");
  });

  it("newId never collides with existing ids", () => {
    const id = ops.newId(list);
    expect(list.some((o) => o.id === id)).toBe(false);
  });

  it("errorsById indexes compile errors by op", () => {
    const byId = ops.errorsById([{ op_id: "b", code: "X" } as any]);
    expect(byId["b"].code).toBe("X");
  });
});

// FINDING 2: onOffer was never wired, so every structured error's offer buttons
// (LABEL_TOO_LONG -> "grow", COMMANDED_CHANNEL -> "acknowledge", CAPABILITY_CHANGE ->
// "acknowledge") were dead. applyOffer is the pure resolution App.tsx's onOffer calls.
describe("applyOffer", () => {
  it('"grow" sets allow_grow on the named op and leaves other ops alone', () => {
    const list = [op("a", "rename_label", { object: 35, old: "x", new: "a much longer one" }),
                  op("b", "recolor", { object: 9, color: { index: 3 } })];
    const out = ops.applyOffer(list, "a", "grow");
    expect(out[0].params.allow_grow).toBe(true);
    expect(out[1]).toEqual(list[1]);   // untouched
  });

  it('"acknowledge" sets allow_commanded_channel for add_button', () => {
    const list = [op("a", "add_button", { channel: 28, label: "Valve" })];
    const out = ops.applyOffer(list, "a", "acknowledge");
    expect(out[0].params.allow_commanded_channel).toBe(true);
  });

  it('"acknowledge" sets allow_commanded_channel for wire_button', () => {
    const list = [op("a", "wire_button", { target: 5, channel: 28 })];
    const out = ops.applyOffer(list, "a", "acknowledge");
    expect(out[0].params.allow_commanded_channel).toBe(true);
  });

  it('"acknowledge" sets allow_capability_change for remap_channel (not allow_commanded_channel)', () => {
    const list = [op("a", "remap_channel", { label: "Bath Fan", channel: 40 })];
    const out = ops.applyOffer(list, "a", "acknowledge");
    expect(out[0].params.allow_capability_change).toBe(true);
    expect(out[0].params.allow_commanded_channel).toBeUndefined();
  });

  it('"acknowledge_named_output" sets allow_named_output, not allow_commanded_channel', () => {
    const list = [op("a", "add_button", { channel: 12, label: "Porch" })];
    const out = ops.applyOffer(list, "a", "acknowledge_named_output");
    expect(out[0].params.allow_named_output).toBe(true);
    expect(out[0].params.allow_commanded_channel).toBeUndefined();
  });

  it('"acknowledge_container" sets allow_container on a recolor', () => {
    const list = [op("a", "recolor", { object: 905, color: [27, 117, 188] })];
    const out = ops.applyOffer(list, "a", "acknowledge_container");
    expect(out[0].params.allow_container).toBe(true);
  });

  it("records the acknowledgement as a note without discarding an existing one", () => {
    const list = [{ ...op("a", "add_button", { channel: 28, label: "Valve" }),
                    note: "group 94" }];
    const out = ops.applyOffer(list, "a", "acknowledge");
    expect(out[0].note).toContain("group 94");
    expect(out[0].note).toContain("acknowledged");
  });

  it("an unhandled offer leaves the op's params unchanged", () => {
    const list = [op("a", "rename_label", { object: 35, old: "x", new: "y" })];
    const out = ops.applyOffer(list, "a", "shorten");
    expect(out[0].params).toEqual(list[0].params);
  });
});

// A table object holds one record per row -- object 35 IS the whole G12 OUTPUTS list.
// Keying a rename on the object alone made every row the same change, so renaming a
// second row silently replaced the first (the reference build renames two of them).
describe("rename identity — one entry per ROW, not per object", () => {
  const rename = (object: number, old: string, next: string) =>
    ({ type: "rename_label", params: { object, old, new: next } });

  it("keeps two rows of the same object as two separate changes", () => {
    let list: OpSpec[] = [];
    list = ops.upsertOp(list, rename(35, "29 - ", "29 - Starlink"));
    list = ops.upsertOp(list, rename(35, "28 - ", "28 - Overflow Valve"));
    expect(list).toHaveLength(2);
    expect(list.map((o) => o.params.new))
      .toEqual(["29 - Starlink", "28 - Overflow Valve"]);
  });

  it("still collapses repeated edits of the SAME row into one change", () => {
    let list: OpSpec[] = [];
    list = ops.upsertOp(list, rename(35, "29 - ", "29 - Star"));
    // The inspector now shows the renamed text, so the next draft's `old` is that --
    // resolved back to stock before the upsert, or it would append a second entry.
    const draft = ops.withResolvedPreImage(list, rename(35, "29 - Star", "29 - Starlink"));
    list = ops.upsertOp(list, draft, ["old"]);
    expect(list).toHaveLength(1);
    expect(list[0].params).toMatchObject({ old: "29 - ", new: "29 - Starlink" });
  });

  it("resolves a pre-image through a chain of renames, and stops when there is none", () => {
    const chain: OpSpec[] = [
      { id: "a", type: "rename_label", enabled: true, note: "",
        params: { object: 35, old: "29 - ", new: "A" } },
      { id: "b", type: "rename_label", enabled: true, note: "",
        params: { object: 35, old: "A", new: "B" } },
    ];
    expect(ops.resolveRenameOld(chain, 35, "B")).toBe("29 - ");
    expect(ops.resolveRenameOld(chain, 35, "untouched")).toBe("untouched");
    expect(ops.resolveRenameOld(chain, 4, "B")).toBe("B");   // a different object
  });

  it("leaves every other op type's draft untouched", () => {
    const draft = { type: "move_element", params: { target: 7, set: { x: 1 } } };
    expect(ops.withResolvedPreImage([], draft)).toBe(draft);
  });
});
