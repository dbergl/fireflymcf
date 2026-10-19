// studio/web/src/panes/AddButton.tsx
// The guided add-a-button card. It emits engine ops -- `add_button` (build-on-target:
// the channel, colour and caption all go in one op, which is why the engine can never
// orphan a command group) plus an optional `set_gesture`.
//
// FINDING 6: unlike the Inspector's remap picker (a different op, `remap_channel`,
// applied to a control that already exists), this card's Pin choice feeds `add_button`
// -> `bind_pin`, which refuses every UNWATCHED pin outright (BIND_REFUSED, no offers)
// before any consent gate even runs. So an "other channels — no status indicator"
// optgroup here can never succeed for a single entry in it -- it is not rendered.
import { useState } from "react";
import type { Colour, Font, Pin } from "../api";
import type { OpDraft } from "./Inspector";
import { PENDING_TARGET } from "../ops";

type Props = {
  pins: Pin[];
  colours: Colour[];
  fonts: Font[];
  onCreate: (drafts: OpDraft[]) => void;
  onCancel: () => void;
};

// The firmware's own button blue -- the resting colour every stock button uses. Kept
// as the DEFAULT only: the picker below is seeded from `/api/reference/colors`, so the
// palette itself is byte-derived and this just decides which entry starts selected.
const BUTTON_BLUE: [number, number, number] = [65, 81, 163];

export default function AddButton(p: Props) {
  const [label, setLabel] = useState("");
  const [pin, setPin] = useState<number | "">("");
  const [gesture, setGesture] = useState("latch");
  const [font, setFont] = useState<number | "">("");
  const [ack, setAck] = useState(false);
  const [offColour, setOffColour] = useState(BUTTON_BLUE.join(","));

  const chosen = p.pins.find((x) => x.pin === pin);
  const needsAck = !!chosen?.warnings.length;
  const ready = label.trim() !== "" && pin !== "" && (!needsAck || ack);

  const create = () => {
    const note = needsAck
      ? chosen!.warnings.join(" ") + " — acknowledged in the editor" : "";
    const drafts: OpDraft[] = [{
      type: "add_button",
      params: {
        channel: Number(pin), label, off_colour: offColour.split(",").map(Number),
        ...(font !== "" ? { font: Number(font) } : {}),
        ...(needsAck ? { allow_commanded_channel: true } : {}),
      },
      note,
    }];
    if (gesture === "hold_to_toggle") {
      drafts.push({ type: "set_gesture",
                    params: { target: PENDING_TARGET, kind: "hold_to_toggle",
                              seconds: 3 } });
    }
    p.onCreate(drafts);
  };

  return (
    <div className="card">
      <h3>Add a button</h3>
      <label htmlFor="ab-label">Caption</label>
      {/* A textarea, not an <input>: a caption can be more than one line, and the
          firmware means it literally -- object 35 ships 'G12\nOUTPUTS' with a 0x0A in
          the middle. An <input> cannot hold a newline at all, so a two-line caption was
          simply unaskable here before. Enter inserts a line break rather than
          submitting; the caption is short, so two rows is the whole field. */}
      <textarea id="ab-label" rows={2} value={label}
                onChange={(e) => setLabel(e.target.value)} />
      {label.includes("\n") && (
        <p className="hint">{label.split("\n").length} lines — drawn one above the other</p>
      )}

      <label htmlFor="ab-pin">Pin</label>
      <select id="ab-pin" value={pin}
              onChange={(e) => { setPin(Number(e.target.value)); setAck(false); }}>
        <option value="" disabled>choose…</option>
        {p.pins.length > 0 && (
          <optgroup label="pins with a status indicator">
            {p.pins.map((x) => (
              <option key={x.pin} value={x.pin}>
                {`pin ${x.pin}${x.commanded ? " · commanded" : ""}`}
              </option>
            ))}
          </optgroup>
        )}
      </select>
      {chosen?.warnings.map((w) => <p key={w} className="warn">{w}</p>)}
      {needsAck && (
        <label className="inline">
          <input type="checkbox" checked={ack} aria-label="acknowledge the warning"
                 onChange={(e) => setAck(e.target.checked)} />
          I understand — wire it anyway
        </label>
      )}

      <label htmlFor="ab-offcolour">Off colour</label>
      <select id="ab-offcolour" value={offColour}
              onChange={(e) => setOffColour(e.target.value)}>
        {p.colours.map((c) => (
          <option key={c.rgb.join(",")} value={c.rgb.join(",")}>
            {`rgb(${c.rgb.join(", ")})`}
          </option>
        ))}
      </select>

      {/* The caption's font. `add_button` takes it as a param, so choosing it here
          keeps the whole button one op -- the alternative (create, then set_font from
          the inspector) is two change-list entries for one decision. Blank leaves the
          donor's own font, which is what every button before this one got. */}
      <label htmlFor="ab-font">Font</label>
      <select id="ab-font" value={font}
              onChange={(e) => setFont(e.target.value === "" ? "" : Number(e.target.value))}>
        <option value="">donor's font</option>
        {p.fonts.map((f) => (
          <option key={f.slot} value={f.slot}>{`slot ${f.slot} · ${f.cell_height}px`}</option>
        ))}
      </select>

      <label htmlFor="ab-gesture">Gesture</label>
      <select id="ab-gesture" value={gesture}
              onChange={(e) => setGesture(e.target.value)}>
        <option value="latch">Tap toggles</option>
        <option value="hold_to_toggle">Hold 3 s to toggle</option>
      </select>

      <div className="card-actions">
        <button disabled={!ready} onClick={create}>create</button>
        <button onClick={p.onCancel}>cancel</button>
      </div>
    </div>
  );
}
