// studio/web/src/ops.ts
// Pure op-list logic. No React, no fetch -- so it is cheap to test exhaustively.
//
// upsertOp implements the spec's editing rule: "Editing a field creates an op -- or
// updates the op already targeting that field, so ten label tweaks stay one
// change-list entry." Identity is (type, target, selector): the same widget's same
// property is one entry; a different widget or a different property is a new one.
import type { OpSpec } from "./api";

export function newId(existing: OpSpec[]): string {
  const taken = new Set(existing.map((o) => o.id));
  for (let n = 1; ; n++) {
    const id = `op${n}`;
    if (!taken.has(id)) return id;
  }
}

export function opTargetKey(op: Pick<OpSpec, "type" | "params">): string {
  const p: any = op.params ?? {};

  // Per-type identity table (verified against src/fireflymcf/recipe_ops.py).
  // Each op type keys its identity by specific param(s); some types have no
  // identity and must always append (caught explicitly below).
  const identityByType: Record<string, (p: any) => string | null> = {
    // Ops with object/target/child + optional selector:
    // Keyed on the record's own pre-image as well as the object: object 35 is the
    // WHOLE G12 OUTPUTS table, so keying on the object alone made every row in it the
    // same change -- renaming row 28 silently replaced the rename of row 29, and the
    // change list showed one entry where the user had made two. `old` is a stock
    // pre-image (see resolveRenameOld), so it stays stable across repeated edits of
    // the same row and those still collapse into one entry.
    rename_label:   (p) => `${p.object ?? null}|${p.old ?? ""}|${selectorStr(p)}`,
    move_element:   (p) => `${targetStr(p.target)}|${selectorStr(p)}`,
    move_attach:    (p) => `${targetStr(p.child)}|${selectorStr(p)}`,
    set_font:       (p) => `${targetStr(p.target)}|${selectorStr(p)}`,
    set_icon:       (p) => `${targetStr(p.target)}|${selectorStr(p)}`,
    recolor:        (p) => `${p.object ?? null}|${selectorStr(p)}`,
    wire_button:    (p) => `${targetStr(p.target)}|${selectorStr(p)}`,
    set_gesture:    (p) => (p.group != null ? `group:${p.group}` : targetStr(p.target)) + `|${selectorStr(p)}`,
    remove_control: (p) => `${p.object ?? null}|${selectorStr(p)}`,

    // Ops with unique identity params:
    remap_channel:  (p) => `${p.label ?? null}|`,
    add_button:     (p) => `${p.channel ?? null}|`,
    install_glyph:  (p) => `${p.slot ?? null}|${p.char ?? null}`,

    // Ops with no identity (always append):
    // NOTE: Returns a sentinel to avoid collisions if opTargetKey is called directly.
    // upsertOp short-circuits add_glyph_button before calling opTargetKey, so this
    // path is not reached during normal upsert. But exported opTargetKey must not
    // share a key across all add_glyph_button instances.
    add_glyph_button: (p) => "<no-identity>",

    // Ops with type-only identity (no target):
    tank_heater_names: (p) => "",
  };

  const getKey = identityByType[op.type];
  if (!getKey) {
    // Unknown op type; fall back to object/target/child logic for forward compat
    const target = p.object ?? p.target ?? p.child ?? null;
    return `${op.type}|${targetStr(target)}|${selectorStr(p)}`;
  }

  const key = getKey(p);
  // key is null only for truly always-append types (currently none after fixing add_glyph_button)
  // but keep the check for clarity
  return `${op.type}|${key}`;
}

function targetStr(target: any): string {
  if (target === null || target === undefined) return "null";
  if (typeof target === "object" && "ref" in target) return `ref:${target.ref}`;
  return String(target);
}

function selectorStr(p: any): string {
  if (!p.selector) return "";
  // Sort keys for stable stringification across {label, wtype} and {wtype, label}
  const sorted: Record<string, any> = {};
  const keys = Object.keys(p.selector).sort();
  for (const k of keys) sorted[k] = p.selector[k];
  return JSON.stringify(sorted);
}

/**
 * The STOCK text of the record a rename targets, by walking back through renames
 * already in the list. A second edit of the same row reads its CURRENT text off the
 * compiled image ('29 - Starlink'), but the op that produced it names the stock text
 * ('29 - '); without this the two would be different identities and every tweak would
 * append another entry instead of updating the one that is already there.
 */
export function resolveRenameOld(ops: OpSpec[], object: unknown,
                                  current: string): string {
  let text = current;
  for (let guard = 0; guard < ops.length + 1; guard++) {
    const prior = ops.find((o) => o.type === "rename_label"
      && o.params.object === object && o.params.new === text);
    if (!prior) return text;
    text = String(prior.params.old ?? text);
  }
  return text;                                   // cyclic list: stop rather than spin
}

export type OpDraft = Omit<OpSpec, "id" | "enabled" | "note">
                     & Partial<Pick<OpSpec, "note">>;

// upsertOp's caller-visible id-of-the-touched-op, for callers (like a multi-draft
// batch) that need to know WHICH op a draft landed on -- whether that op was just
// appended (a fresh id) or an existing one was updated in place (its existing id).
// Inferring this by diffing the list before/after is a trap: an update produces no
// new id, so a diff sees nothing and a caller expecting "the id of what I just did"
// gets null on the very common case of re-submitting the same identity twice.
// FINDING 1 fix (final-review, 2026-08-20): an op that carries a PRE-IMAGE --
// move_element's `expect`, rename_label's `old` -- must always describe STOCK, because
// the engine applies the single collapsed op against stock, not against whatever the
// client last saw. But `upsertOp` replaces `params` wholesale, and the client builds a
// fresh draft from the CURRENTLY COMPILED image on every edit -- so a second drag reads
// `expect` off the post-first-drag placement, and a second rename reads `old` off the
// post-first-rename label. Both go stale on the second edit and the engine's guard
// (correctly) refuses them.
//
// `preserve` names the param keys to keep from the EXISTING op, when this draft lands
// on an update rather than an append: the caller still supplies a full (necessarily
// stale) draft, but the keys in `preserve` are overwritten back to whatever the
// existing op already had, before the merge. On an append there is no existing op to
// preserve from, so `preserve` has no effect -- the fresh draft's own pre-image is
// already stock-accurate the first time.
function applyPreserve(draftParams: Record<string, unknown>,
                        existingParams: Record<string, unknown>,
                        preserve: string[] | undefined): Record<string, unknown> {
  if (!preserve || preserve.length === 0) return draftParams;
  const merged = { ...draftParams };
  for (const key of preserve) {
    if (key in existingParams) merged[key] = existingParams[key];
  }
  return merged;
}

export function upsertOpWithId(
  ops: OpSpec[],
  draft: OpDraft,
  preserve?: string[],
): { ops: OpSpec[]; id: string } {
  // add_glyph_button has no identity — each call creates a NEW object.
  // Always append, never collapse.
  if (draft.type === "add_glyph_button") {
    const id = newId(ops);
    return { ops: [...ops, { id, type: draft.type, params: draft.params,
                             enabled: true, note: draft.note ?? "" }], id };
  }

  const key = opTargetKey(draft);
  const at = ops.findIndex((o) => opTargetKey(o) === key);
  if (at === -1) {
    const id = newId(ops);
    return { ops: [...ops, { id, type: draft.type, params: draft.params,
                             enabled: true, note: draft.note ?? "" }], id };
  }
  const next = [...ops];
  const existing = next[at];
  const params = applyPreserve(draft.params, existing.params, preserve);
  next[at] = { ...existing, params, note: draft.note ?? existing.note };
  return { ops: next, id: next[at].id };
}

export function upsertOp(ops: OpSpec[], draft: OpDraft, preserve?: string[]): OpSpec[] {
  return upsertOpWithId(ops, draft, preserve).ops;
}

// A same-batch draft (e.g. AddButton's optional set_gesture) can't know the real op
// id an EARLIER draft in the same batch (e.g. its add_button) will land on -- that id
// is only decided once the earlier draft is actually applied, and per upsertOpWithId's
// note above, it may be a REUSED id, not a fresh one. This object's IDENTITY, not its
// shape, is the marker: applyOpDrafts substitutes it by reference, so no sentinel
// string can leak into a saved project the way a JSON search-and-replace (or an id
// inferred by diffing before/after arrays, which silently fails on an update) can.
export const PENDING_TARGET = { ref: "__pending_add_button__" };

// Folds a batch of drafts (as produced by AddButton.onCreate) into an op list in one
// pass, substituting PENDING_TARGET in every later draft's `params.target` with
// `{ ref: id }` of whatever op the FIRST draft in the batch touched -- appended or
// updated. Pure and exported so the substitution itself is unit-testable, not just
// exercised incidentally through a component test.
export function applyOpDrafts(ops: OpSpec[], drafts: OpDraft[]): OpSpec[] {
  let next = ops;
  let firstId: string | null = null;
  for (const draft of drafts) {
    const params = (firstId !== null && draft.params.target === PENDING_TARGET)
      ? { ...draft.params, target: { ref: firstId } }
      : draft.params;
    // The substitution above only fires from the SECOND draft onward (firstId is
    // still null on the first iteration). That is correct for AddButton's own
    // batches -- add_button always leads -- but this function is general-purpose,
    // and nothing here enforces that ordering on a caller. A draft that still
    // carries the sentinel after the substitution attempt, at ANY position, is
    // exactly the corruption upsertOpWithId's callers exist to prevent: a raw
    // {ref: "__pending_add_button__"} would otherwise be saved as a real target.
    // Refuse loudly rather than persist it.
    if (params.target === PENDING_TARGET) {
      throw new Error(
        "applyOpDrafts: a draft targets PENDING_TARGET with no preceding op to "
        + "resolve it");
    }
    const result = upsertOpWithId(next, { ...draft, params });
    next = result.ops;
    if (firstId === null) firstId = result.id;
  }
  return next;
}

export function reorderOp(ops: OpSpec[], from: number, to: number): OpSpec[] {
  const next = [...ops];
  const [moved] = next.splice(from, 1);
  next.splice(to, 0, moved);
  return next;
}

export const toggleOp = (ops: OpSpec[], id: string): OpSpec[] =>
  ops.map((o) => (o.id === id ? { ...o, enabled: !o.enabled } : o));
export const removeOp = (ops: OpSpec[], id: string): OpSpec[] =>
  ops.filter((o) => o.id !== id);
export const setNote = (ops: OpSpec[], id: string, note: string): OpSpec[] =>
  ops.map((o) => (o.id === id ? { ...o, note } : o));

export function errorsById<T extends { op_id: string | null }>(
  errors: T[],
): Record<string, T> {
  const out: Record<string, T> = {};
  for (const e of errors) if (e.op_id) out[e.op_id] = e;
  return out;
}

// FINDING 2 fix: a structured compile error's `offers` are the only remediation route
// for LABEL_TOO_LONG ("grow"), COMMANDED_CHANNEL ("acknowledge", add_button/wire_button)
// and CAPABILITY_CHANGE ("acknowledge", remap_channel) -- but nothing ever called them,
// so the offer buttons in ChangeList were dead. `applyOffer` is the pure resolution:
// find the named op, set the flag the recipe engine's own error message says to set,
// and append a short note recording the acknowledgement (never overwriting an existing
// one, so an earlier judgement stays legible). Other offers this project ships
// ("shorten", "use_convention", "force", "disable_gesture_op") name a fix that needs
// the user to change something themselves -- there is no flag to flip, so the op is
// returned unchanged rather than guessing at one.
export function applyOffer(ops: OpSpec[], id: string, offer: string): OpSpec[] {
  return ops.map((o) => {
    if (o.id !== id) return o;
    if (offer === "grow") {
      return { ...o, params: { ...o.params, allow_grow: true },
               note: appendNote(o.note, "grow acknowledged in the editor") };
    }
    if (offer === "acknowledge_named_output" || offer === "acknowledge_container") {
      const key = offer === "acknowledge_named_output" ? "allow_named_output" : "allow_container";
      return { ...o, params: { ...o.params, [key]: true },
               note: appendNote(o.note, "acknowledged in the editor") };
    }
    if (offer === "acknowledge") {
      const key = o.type === "remap_channel" ? "allow_capability_change"
                                              : "allow_commanded_channel";
      return { ...o, params: { ...o.params, [key]: true },
               note: appendNote(o.note, "acknowledged in the editor") };
    }
    return o;
  });
}

function appendNote(existing: string, addition: string): string {
  return existing ? `${existing}; ${addition}` : addition;
}

/**
 * Rewrite a draft's pre-image so it names STOCK, not the currently compiled image.
 * Only `rename_label` carries one that the client can resolve on its own: `old` comes
 * from the label the inspector is showing, which is post-rename once an earlier op has
 * already renamed that record. `move_element`'s `expect` is handled the other way, by
 * `upsertOp`'s `preserve` (there is no equivalent chain to walk: geometry ops do not
 * name their predecessor's output the way a rename names its old text).
 */
export function withResolvedPreImage(ops: OpSpec[], draft: OpDraft): OpDraft {
  if (draft.type !== "rename_label") return draft;
  const current = String(draft.params.old ?? "");
  const stock = resolveRenameOld(ops, draft.params.object, current);
  return stock === current
    ? draft : { ...draft, params: { ...draft.params, old: stock } };
}
