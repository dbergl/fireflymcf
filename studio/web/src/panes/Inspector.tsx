// studio/web/src/panes/Inspector.tsx
// The selected control's editable fields. Each control maps to exactly one engine op
// (the parent upserts, so repeated edits stay one change-list entry). Nothing here
// knows firmware bytes: the field list comes from each placement's `editable`, the
// colours and fonts from the reference endpoints.
import { useEffect, useState } from "react";
import * as api from "../api";
import type { Binding, Channel, Colour, Font, OpSchema, Pin } from "../api";
import type { Selection } from "../selection";
import type { OpDraft } from "../ops";
import { GEOM_KEYS, geomOf, moveElementDraft, resizeControlDrafts, selectorFor }
  from "../geomedit";
import type { GeomKey, GeomValues } from "../geomedit";

// Re-exported so existing `import type { OpDraft } from "./Inspector"` call sites
// (AddButton.tsx, and any future pane) keep working -- ops.ts is the single
// definition now (it's also where the fold that consumes OpDraft batches lives),
// this file no longer defines its own structurally-identical copy.
export type { OpDraft };

// Every string `gestures.GroupBehaviour.kind_for_variant` can return (src/fireflymcf/
// gestures.py, transcribed at cli.py's `_GESTURE_KINDS`), worded in plain language --
// never a phase mask, nibble or hex value. `tap-or-hold` is every dimmable light, the
// most common control on the coach, so it must never fall through to the raw token.
const GESTURE_WORDS: Record<string, string> = {
  latch: "Tap toggles",
  "tap-or-hold": "Tap toggles, hold to dim",
  "hold-only": "Hold to toggle",
  "hold-release": "Hold to run, release to stop",
  "tap-only": "Tap only",
  "press-only": "Acts on press",
  momentary: "Momentary while held",
};

// The firmware's own button blue -- the resting colour every added button uses. Kept
// as the DEFAULT only: the off-colour picker below is seeded from
// `/api/reference/colors`, so the palette is byte-derived and this is just which
// entry starts selected.
const BUTTON_BLUE: [number, number, number] = [65, 81, 163];

type Props = {
  selection: Selection | null;
  /** The page being edited — `move_attach` names it. */
  page?: string;
  binding: Binding | null;
  colours: Colour[];
  fonts: Font[];
  pins: Pin[];
  channels?: Channel[];
  schemas: Record<string, OpSchema>;
  onOp: (draft: OpDraft) => void;
  /** Apply several ops as one gesture (resizing a control is three records). */
  onOps?: (drafts: OpDraft[]) => void;
  onRemoveControl: () => void;
};

export default function Inspector(p: Props) {
  // The clicked record wins when it is itself text. Falling back to "the first label
  // in the object" is right for a button (its caption is the only one) and wrong for a
  // table: clicking row '28 - ' of the G12 OUTPUTS object used to offer the first row's
  // text instead, so the row a person pointed at was not the row they could rename.
  const isText = (x: { kind: string }) => x.kind === "label" || x.kind === "text";
  // Optional-chained on `hit` as well as `selection`: every real selection comes from
  // `selectAt`, which always sets it, but a hand-built selection (a test fixture, or a
  // future caller that selects by index rather than by click) legitimately has no
  // clicked record, and the object-wide fallback is exactly right for those.
  const caption = p.selection?.hit && isText(p.selection.hit)
    ? p.selection.hit
    : p.selection?.placements.find(isText);
  const box = p.selection?.placements.find((x) => x.kind === "box");
  const [label, setLabel] = useState(caption?.label ?? "");
  const [grow, setGrow] = useState(false);
  const [colourIdx, setColourIdx] = useState("");

  const [pin, setPin] = useState<number | "">("");
  const [ack, setAck] = useState(false);
  const [moveDisplayList, setMoveDisplayList] = useState(false);

  // Wire-to-a-pin (`wire_button`): its own pin/ack pair rather than sharing the remap
  // section's, because the two are never offered together (see `unwired` below) and a
  // shared ack would be consent carried between two different ops.
  const [wirePin, setWirePin] = useState<number | "">("");
  const [wireAck, setWireAck] = useState(false);
  const [offColour, setOffColour] = useState(BUTTON_BLUE.join(","));

  const [iconSlot, setIconSlot] = useState<number | "">("");
  const [iconChar, setIconChar] = useState("");
  const [blankCaption, setBlankCaption] = useState(false);

  // Geometry is per-RECORD, not per-object: a control is a box, a caption and a touch
  // region that move and size independently ("Visual, touch and text are separate;
  // moving one leaves the others behind"). So the form holds one set of values per
  // placement, keyed by its position in the selection.
  const [geom, setGeom] = useState<Record<number, GeomValues>>({});
  // Whole-control size, and the control's position on the page (its attach record).
  const [ctrl, setCtrl] = useState<{ w: string; h: string; x: string }>(
    { w: "", h: "", x: "" });
  const [attach, setAttach] = useState<{ x: string; y: string }>({ x: "", y: "" });

  useEffect(() => { setLabel(caption?.label ?? ""); setGrow(false); setColourIdx("");
                     setPin(""); setAck(false); setMoveDisplayList(false);
                     setWirePin(""); setWireAck(false);
                     setOffColour(BUTTON_BLUE.join(","));
                     setIconSlot(""); setIconChar(""); setBlankCaption(false);
                     setGeom({}); setCtrl({ w: "", h: "", x: "" });
                     setAttach({ x: "", y: "" }); },
            [caption?.label, p.selection?.obj_index]);

  const longer = caption ? label.length > (caption.label ?? "").length : false;

  // Grow consent must never survive the checkbox disappearing -- a relocating build
  // is materially riskier than a same-size edit (this project boot-looped a coach on
  // one), so if the label is shortened back under the field width the consent is
  // gone, not just hidden, and re-lengthening shows the checkbox unchecked again.
  useEffect(() => { if (!longer) setGrow(false); }, [longer]);

  const unwatchedChannels = (p.channels ?? []).filter((c) => !c.watched);
  const chosen = p.pins.find((x) => x.pin === pin);
  const needsAck = !!chosen?.warnings.length;

  // A control with no binding has nothing to remap -- it needs wiring, which is a
  // different op. `binding === null` is the honest signal for that: it comes from the
  // server's own group/binding decode, not from a guess about what kind of object
  // this is. The two sections are therefore mutually exclusive by construction.
  const unwired = !p.binding && !!box?.editable.includes("wire_button");
  const wireChosen = p.pins.find((x) => x.pin === wirePin);
  const wireNeedsAck = !!wireChosen?.warnings.length;
  const gestureNow = p.binding ? (GESTURE_WORDS[p.binding.gesture]
                                  ?? "Custom gesture") : null;
  const authorable = !p.binding || p.binding.gesture === "latch"
                      || p.binding.gesture === "hold-only";

  if (!p.selection) return <p className="muted">Nothing selected. Click a control.</p>;

  const obj = p.selection.obj_index;
  const allowGrow = longer && grow;
  const geomRows = p.selection.placements
    .map((pl, i) => ({ pl, i }))
    .filter(({ pl }) => pl.geom && pl.editable.includes("move_element"));

  // Every row needs a name a person (and a screen reader) can tell apart: object 902
  // carries BOTH the House and the Chassis caption, so "label 11" named two different
  // records and neither could be addressed unambiguously. A caption is named by its own
  // text; anything else by kind and wtype; and a name that still repeats gets a number.
  const baseName = (pl: typeof geomRows[number]["pl"]) =>
    pl.kind === "label" && pl.label
      ? `${pl.kind} “${pl.label.replace(/\n/g, " ").trim()}”`
      : `${pl.kind} ${pl.wtype}`;
  const rowName = (row: typeof geomRows[number]) => {
    const base = baseName(row.pl);
    const same = geomRows.filter((r) => baseName(r.pl) === base);
    return same.length > 1
      ? `${base} #${same.findIndex((r) => r.i === row.i) + 1}` : base;
  };

  return (
    <div className="inspector-body">
      <h3>Object {obj}</h3>

      {caption && caption.editable.includes("rename_label") && (
        <section>
          <label htmlFor="insp-label">Label</label>
          <input id="insp-label" value={label}
                 onChange={(e) => setLabel(e.target.value)} />
          {longer && (
            <label className="inline">
              {/* This is the riskiest consent in the app (a relocating build, not a
                  same-size edit -- this project boot-looped a coach on one), so the
                  accessible name must carry the same caveat the sighted label shows,
                  not a trimmed-down "grow the field" that drops it for screen readers. */}
              <input type="checkbox" checked={grow}
                     aria-label="grow the field — longer than the field, relocating build"
                     onChange={(e) => setGrow(e.target.checked)} />
              longer than the field — grow it (relocating build)
            </label>
          )}
          <button onClick={() => p.onOp({
            type: "rename_label",
            params: { object: obj, old: caption.label ?? "", new: label,
                      ...(allowGrow ? { allow_grow: true } : {}) },
          })}>apply label</button>
        </section>
      )}

      {caption && caption.editable.includes("set_font") && (
        <section>
          <label htmlFor="insp-font">Font</label>
          <select id="insp-font" value={caption.font ?? ""}
                  onChange={(e) => p.onOp({
                    type: "set_font",
                    // The selector names the record that was CLICKED, not "the
                    // object's first wtype-11". Object 902 carries two captions and
                    // object 35 carries a hundred rows -- a hardcoded nth-0 selector
                    // set the font of a different record than the one on screen, and
                    // now that a text row can be selected at all it would be a
                    // different WTYPE too.
                    params: { target: obj,
                              selector: selectorFor(caption,
                                                    p.selection!.placements),
                              slot: Number(e.target.value) },
                  })}>
            {caption.font === null && (
              <option value="" disabled>inherited</option>
            )}
            {p.fonts.map((f) => (
              <option key={f.slot} value={f.slot}>
                slot {f.slot} · {f.cell_height}px
              </option>
            ))}
          </select>
          {caption.font !== null && (
            <img className="specimen" alt={`font ${caption.font} specimen`}
                 src={api.fontSpecimenUrl(caption.font)} />
          )}
        </section>
      )}

      {/* One size for the whole control. A control is three records that size
          independently, and a button whose touch region is still the donor's size is a
          button that does not respond where it looks like it should -- so the common
          case is all three at once, and the per-record rows below are the exception. */}
      {box && geomRows.length > 1 && p.onOps && (
        <section className="control-size">
          <h4>Control size</h4>
          <div className="fieldrow">
            {(["w", "h", "x"] as const).map((k) => (
              <label key={k} className="geomfield">
                {k}
                <input aria-label={`control ${k}`} inputMode="numeric"
                       placeholder={String(geomOf(box)[k] ?? "—")}
                       value={ctrl[k]}
                       onChange={(e) => setCtrl({ ...ctrl, [k]: e.target.value })} />
              </label>
            ))}
          </div>
          <button onClick={() => {
            const num = (v: string) => (v.trim() === "" ? undefined : Number(v));
            const drafts = resizeControlDrafts(obj, p.selection!.placements, {
              w: num(ctrl.w), h: num(ctrl.h), x: num(ctrl.x) });
            if (drafts.length) p.onOps!(drafts);
          }}>resize control</button>
          <p className="hint">
            Applies to the box, the caption and the touch region together.
          </p>
        </section>
      )}

      {/* Where the control sits on the page. This is its ATTACH record, not its own
          geometry: a control is positioned by the page that carries it, which is why
          nudging the box alone leaves the caption and touch region behind. Editing it
          here means never having to look up the object index for a raw move_attach. */}
      {box && p.page && (
        <section className="attach-position">
          <h4>Position on page</h4>
          <div className="fieldrow">
            {(["x", "y"] as const).map((k) => (
              <label key={k} className="geomfield">
                {k}
                <input aria-label={`page position ${k}`} inputMode="numeric"
                       placeholder="unchanged" value={attach[k]}
                       onChange={(e) => setAttach({ ...attach, [k]: e.target.value })} />
              </label>
            ))}
          </div>
          <button disabled={attach.x.trim() === "" && attach.y.trim() === ""}
                  onClick={() => p.onOp({
                    type: "move_attach",
                    params: {
                      page: p.page, child: obj,
                      ...(attach.x.trim() !== "" ? { x: Number(attach.x) } : {}),
                      ...(attach.y.trim() !== "" ? { y: Number(attach.y) } : {}),
                    },
                  })}>move on page</button>
        </section>
      )}

      {/* Size and position, one row per record. A drag on the canvas can only express a
          translation of a label, so without this there is no route to a resize at all --
          and resizing every record of a control (box, caption, touch) is what makes an
          added button a different shape from its donor. Each row writes only the fields
          the record actually carries; the rest are shown as "—" rather than offered and
          then refused with NO_FIELD. */}
      {geomRows.length > 0 && (
        <section className="geometry">
          <h4>Size &amp; position</h4>
          {geomRows.map((row) => {
            const { pl, i } = row;
            const now = geomOf(pl);
            const name = rowName(row);
            return (
              <div key={i} className="geomrow">
                <div className="muted">{name}</div>
                {GEOM_KEYS.map((k: GeomKey) => (
                  <label key={k} className="geomfield">
                    {k}
                    {now[k] === null || now[k] === undefined ? (
                      <span className="muted" aria-label={`${k} not on this record`}>—</span>
                    ) : (
                      <input aria-label={`${name} ${k}`}
                             inputMode="numeric"
                             placeholder={String(now[k])}
                             value={geom[i]?.[k] ?? ""}
                             onChange={(e) => setGeom((prev) => ({
                               ...prev, [i]: { ...prev[i], [k]: e.target.value } }))} />
                    )}
                  </label>
                ))}
                <button aria-label={`apply geometry to ${name}`}
                        onClick={() => {
                          const draft = moveElementDraft(
                            obj, pl, p.selection!.placements, geom[i] ?? {});
                          if (draft) p.onOp(draft);
                        }}>apply size</button>
              </div>
            );
          })}
        </section>
      )}

      {box && box.editable.includes("recolor") && (
        <section>
          <label htmlFor="insp-colour">Colour</label>
          <select id="insp-colour" value={colourIdx}
                  onChange={(e) => {
                    setColourIdx(e.target.value);
                    p.onOp({
                      type: "recolor",
                      params: { object: obj, color: { index: Number(e.target.value) } },
                    });
                  }}>
            <option value="" disabled>choose…</option>
            {p.colours.flatMap((c) =>
              c.static.map((idx) => (
                <option key={`s${idx}`} value={idx}>
                  {`rgb(${c.rgb.join(",")}) · idx ${idx}`}
                </option>
              )))}
            {p.colours.flatMap((c) =>
              c.resting.map((idx) => (
                <option key={`r${idx}`} value={idx}>
                  {`rgb(${c.rgb.join(",")}) · idx ${idx} (state-coupled)`}
                </option>
              )))}
          </select>
        </section>
      )}

      {p.binding && (
        <section>
          <label htmlFor="insp-pin">Pin</label>
          <div className="muted">
            currently {p.binding.channels.join(", ") || "unbound"}
          </div>
          <select id="insp-pin" value={pin}
                  onChange={(e) => { setPin(Number(e.target.value)); setAck(false); }}>
            <option value="" disabled>choose…</option>
            {p.pins.length > 0 && (
              <optgroup label="pins with a status indicator">
                {p.pins.map((x) => (
                  <option key={x.pin} value={x.pin}>
                    {`pin ${x.pin}${x.commanded ? " · commanded" : ""}`}
                    {x.dimmable ? " · dimmable" : ""}
                  </option>
                ))}
              </optgroup>
            )}
            {unwatchedChannels.length > 0 && (
              <optgroup label="other channels — no status indicator">
                {unwatchedChannels.map((c) => (
                  <option key={c.channel} value={c.channel}>
                    {`channel ${c.channel}${c.commanded ? " · commanded" : ""}`}
                  </option>
                ))}
              </optgroup>
            )}
          </select>
          {chosen?.warnings.map((w, i) => (
            <p key={i} className="warn">{w}</p>
          ))}
          {needsAck && (
            <label className="inline">
              <input type="checkbox" checked={ack} aria-label="acknowledge the warning"
                     onChange={(e) => setAck(e.target.checked)} />
              I understand — wire it anyway
            </label>
          )}
          {/* remap_channel's two status paths. Unchecked (the default) retargets the
              button's own indicator at the new pin and leaves every G12 OUTPUTS row
              honest, but has not been on the coach. Checked moves the status variable
              instead: coach-proven (PC.14, v9), and the old channel's diagnostics row
              reports the new one from then on. Worded from the trade-off, not the flag. */}
          <label className="inline">
            <input type="checkbox" checked={moveDisplayList}
                   aria-label="move the status variable instead (coach-proven)"
                   onChange={(e) => setMoveDisplayList(e.target.checked)} />
            move the status variable instead (coach-proven) — the old channel's
            G12 OUTPUTS row will then report the new one
          </label>
          <button disabled={pin === "" || (needsAck && !ack)}
                  onClick={() => p.onOp({
                    type: "remap_channel",
                    params: { label: caption?.label ?? "", channel: Number(pin),
                              move_display_list: moveDisplayList },
                    // The "" is load-bearing, not a placeholder: ops.ts's upsert merges
                    // `note: draft.note ?? next[at].note`, so an explicit "" clears a
                    // stale note from a prior acknowledged pick instead of leaking it
                    // through onto an unwarned one.
                    note: needsAck ? (chosen!.warnings.join(" ")
                                      + " — acknowledged in the editor") : "",
                  })}>apply pin</button>
        </section>
      )}

      {p.binding && (
        <section>
          <label htmlFor="insp-gesture">Gesture</label>
          <div className="muted">{gestureNow}</div>
          <select id="insp-gesture" disabled={!authorable} defaultValue=""
                  onChange={() => {
                    // The only selectable option below is "hold_to_toggle" -- the
                    // placeholder is `disabled` so it can never be the value an onChange
                    // fires with, and there is no other option. A value-keyed ternary
                    // here always took the same branch; the unreachable "latch" arm is
                    // gone, not just unused.
                    p.onOp({
                      type: "set_gesture",
                      params: { target: obj, kind: "hold_to_toggle", seconds: 3 },
                    });
                  }}>
            {/* No "latch" option: the engine's SetGesture rejects kind:"latch" as
                NOT_SUPPORTED unless the group is already latch, so offering it here
                is a guaranteed-fail choice everywhere it isn't also a no-op. */}
            <option value="" disabled>change…</option>
            <option value="hold_to_toggle">Hold 3 s to toggle</option>
          </select>
        </section>
      )}

      {/* `wire_button` binds the pin's status cell AND retargets the command, so the
          label says both. Calling this "status" (as the design sketch did) would
          misdescribe what the engine actually does to the image. */}
      {unwired && (
        <section>
          <label htmlFor="insp-wire">Wire to a pin</label>
          <div className="muted">
            not wired yet — wiring sets both the indicator and the command
          </div>
          <select id="insp-wire" value={wirePin}
                  onChange={(e) => { setWirePin(Number(e.target.value));
                                     setWireAck(false); }}>
            <option value="" disabled>choose…</option>
            {p.pins.map((x) => (
              <option key={x.pin} value={x.pin}>
                {`pin ${x.pin}${x.commanded ? " · commanded" : ""}`}
              </option>
            ))}
          </select>
          {/* Only watched pins are offered: `wire_button` calls bind_pin, which needs a
              watched var, so an unwatched channel could never succeed here. */}
          <label htmlFor="insp-offcolour">Off colour</label>
          <select id="insp-offcolour" value={offColour}
                  onChange={(e) => setOffColour(e.target.value)}>
            {p.colours.map((c) => (
              <option key={c.rgb.join(",")} value={c.rgb.join(",")}>
                {`rgb(${c.rgb.join(", ")})`}
              </option>
            ))}
          </select>
          {wireChosen?.warnings.map((w, i) => (
            <p key={i} className="warn">{w}</p>
          ))}
          {wireNeedsAck && (
            <label className="inline">
              <input type="checkbox" checked={wireAck}
                     aria-label="acknowledge the warning"
                     onChange={(e) => setWireAck(e.target.checked)} />
              I understand — wire it anyway
            </label>
          )}
          <button disabled={wirePin === "" || (wireNeedsAck && !wireAck)}
                  onClick={() => p.onOp({
                    type: "wire_button",
                    params: {
                      target: obj, channel: Number(wirePin),
                      off_colour: offColour.split(",").map(Number),
                      ...(wireNeedsAck ? { allow_commanded_channel: true } : {}),
                    },
                    // Explicit "" on the unwarned branch, same as the remap section:
                    // ops.ts merges `note ?? existing`, so this clears a stale
                    // acknowledgement rather than leaking it onto an unwarned pin.
                    note: wireNeedsAck ? (wireChosen!.warnings.join(" ")
                                          + " — acknowledged in the editor") : "",
                  })}>wire it</button>
        </section>
      )}

      {/* An icon IS a character in an icon font, so the controls are a font slot and a
          character. Offered whenever the box advertises `set_icon`: whether the object
          actually HAS an icon record is something only the engine can answer (the
          placement's `font` is the effective font after the persistent selector, so it
          does not prove one), and it answers with a structured NO_SUCH_RECORD that the
          change list shows — the same way an unwireable pin already behaves. */}
      {box?.editable.includes("set_icon") && (
        <section>
          <label htmlFor="insp-icon-font">Icon font</label>
          <select id="insp-icon-font" value={iconSlot}
                  onChange={(e) => setIconSlot(e.target.value === ""
                    ? "" : Number(e.target.value))}>
            <option value="">leave unchanged</option>
            {p.fonts.map((f) => (
              <option key={f.slot} value={f.slot}>
                {`slot ${f.slot} · ${f.cell_height}px`}
              </option>
            ))}
          </select>
          {iconSlot !== "" && (
            <img className="specimen" alt={`font ${iconSlot} specimen`}
                 src={api.fontSpecimenUrl(Number(iconSlot))} />
          )}
          <label htmlFor="insp-icon-char">Icon character</label>
          <input id="insp-icon-char" value={iconChar} maxLength={1}
                 placeholder="leave empty to keep"
                 onChange={(e) => setIconChar(e.target.value)} />
          <label className="inline">
            <input type="checkbox" checked={blankCaption}
                   aria-label="blank the caption"
                   onChange={(e) => setBlankCaption(e.target.checked)} />
            blank the caption — makes this a glyph-only button
          </label>
          <button disabled={iconSlot === "" && iconChar === "" && !blankCaption}
                  onClick={() => p.onOp({
                    type: "set_icon",
                    params: {
                      target: obj,
                      ...(iconSlot !== "" ? { slot: Number(iconSlot) } : {}),
                      ...(iconChar !== "" ? { char: iconChar } : {}),
                      ...(blankCaption ? { blank_caption: true } : {}),
                    },
                  })}>apply icon</button>
        </section>
      )}

      <section>
        <button className="danger" onClick={p.onRemoveControl}>Remove control</button>
        {p.schemas.remove_control?.hardware_verified === false && (
          <p className="warn">
            Parking a control off-canvas has never been flashed to a coach. It is
            flagged in every build's manifest as hardware-unverified.
          </p>
        )}
      </section>
    </div>
  );
}
