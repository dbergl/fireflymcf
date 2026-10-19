// studio/web/src/panes/GlyphButton.tsx
// A bare button shell, wired in the same gesture that creates it.
//
// WHY THIS IS ONE CARD AND NOT TWO. `add_glyph_button` clones a donor verbatim, which
// copies the donor's live command group with it: an unwired clone of donor 247 is a
// touch target that still drives the donor's channel. The engine refuses to finish such
// a build ("shell (object N) was never wired") and says so in its own docstring: "pair
// this op with wire_button before shipping it".
//
// Offering the shell alone -- which the generated-form catalogue did -- therefore put
// the editor in a state it could not get out of: the project stopped compiling, and
// because placements are only fetched for a SUCCESSFUL compile, the new object could
// not be clicked, so the inspector could not be used to wire it. The op was reachable
// and uncompletable. Emitting the pair as one batch (the same PENDING_TARGET fold
// AddButton already uses for its optional gesture) makes the unfinished state
// unreachable instead of merely discouraged.
import { useState } from "react";
import type { Colour, Font, Pin } from "../api";
import type { OpDraft } from "../ops";
import { PENDING_TARGET } from "../ops";

type Props = {
  pins: Pin[];
  colours: Colour[];
  fonts: Font[];
  pages: { name: string; root: number }[];
  onCreate: (drafts: OpDraft[]) => void;
  onCancel: () => void;
};

const BUTTON_BLUE: [number, number, number] = [65, 81, 163];

export default function GlyphButton(p: Props) {
  const [page, setPage] = useState("home");
  const [donor, setDonor] = useState("247");
  const [x, setX] = useState("630");
  const [y, setY] = useState("388");
  const [w, setW] = useState("120");
  const [h, setH] = useState("60");
  const [fillCell, setFillCell] = useState("164");
  const [pin, setPin] = useState<number | "">("");
  const [ack, setAck] = useState(false);
  const [offColour, setOffColour] = useState(BUTTON_BLUE.join(","));
  const [iconSlot, setIconSlot] = useState<number | "">("");
  const [gesture, setGesture] = useState("latch");

  const chosen = p.pins.find((v) => v.pin === pin);
  const needsAck = !!chosen?.warnings.length;
  const nums = [x, y, w, h, donor, fillCell];
  const ready = pin !== "" && (!needsAck || ack)
    && nums.every((v) => v.trim() !== "" && Number.isInteger(Number(v)));

  const create = () => {
    const drafts: OpDraft[] = [{
      type: "add_glyph_button",
      params: { page, donor: Number(donor), x: Number(x), y: Number(y),
                w: Number(w), h: Number(h), fill_cell: Number(fillCell) },
    }, {
      // Always emitted, never optional: see the note at the top of this file.
      type: "wire_button",
      params: { target: PENDING_TARGET, channel: Number(pin),
                off_colour: offColour.split(",").map(Number),
                ...(needsAck ? { allow_commanded_channel: true } : {}) },
      note: needsAck ? chosen!.warnings.join(" ") + " — acknowledged in the editor" : "",
    }];
    if (iconSlot !== "") {
      drafts.push({ type: "set_icon",
                    params: { target: PENDING_TARGET, slot: Number(iconSlot),
                              blank_caption: true } });
    }
    if (gesture === "hold_to_toggle") {
      drafts.push({ type: "set_gesture",
                    params: { target: PENDING_TARGET, kind: "hold_to_toggle",
                              seconds: 3 } });
    }
    p.onCreate(drafts);
  };

  return (
    <div className="card wide">
      <h3>Add a glyph button</h3>
      <p className="muted">
        A button that carries an icon instead of a caption. It is cloned from a donor
        control and wired to a pin in one step — a shell left unwired would still drive
        the donor's own channel, so the two are never separated.
      </p>

      <label htmlFor="gb-page">Page</label>
      <select id="gb-page" value={page} onChange={(e) => setPage(e.target.value)}>
        {p.pages.map((pg) => (
          <option key={pg.name} value={pg.name}>{pg.name}</option>
        ))}
      </select>

      <div className="fieldrow">
        <label htmlFor="gb-x">x</label>
        <input id="gb-x" inputMode="numeric" value={x}
               onChange={(e) => setX(e.target.value)} />
        <label htmlFor="gb-y">y</label>
        <input id="gb-y" inputMode="numeric" value={y}
               onChange={(e) => setY(e.target.value)} />
        <label htmlFor="gb-w">width</label>
        <input id="gb-w" inputMode="numeric" value={w}
               onChange={(e) => setW(e.target.value)} />
        <label htmlFor="gb-h">height</label>
        <input id="gb-h" inputMode="numeric" value={h}
               onChange={(e) => setH(e.target.value)} />
      </div>

      <label htmlFor="gb-pin">Pin</label>
      <select id="gb-pin" value={pin}
              onChange={(e) => { setPin(Number(e.target.value)); setAck(false); }}>
        <option value="" disabled>choose…</option>
        {p.pins.map((v) => (
          <option key={v.pin} value={v.pin}>
            {`pin ${v.pin}${v.commanded ? " · commanded" : ""}`}
          </option>
        ))}
      </select>
      {chosen?.warnings.map((warning) => (
        <p key={warning} className="warn">{warning}</p>
      ))}
      {needsAck && (
        <label className="inline">
          <input type="checkbox" checked={ack} aria-label="acknowledge the warning"
                 onChange={(e) => setAck(e.target.checked)} />
          I understand — wire it anyway
        </label>
      )}

      <label htmlFor="gb-offcolour">Off colour</label>
      <select id="gb-offcolour" value={offColour}
              onChange={(e) => setOffColour(e.target.value)}>
        {p.colours.map((c) => (
          <option key={c.rgb.join(",")} value={c.rgb.join(",")}>
            {`rgb(${c.rgb.join(", ")})`}
          </option>
        ))}
      </select>

      <label htmlFor="gb-icon">Icon font</label>
      <select id="gb-icon" value={iconSlot}
              onChange={(e) => setIconSlot(e.target.value === ""
                ? "" : Number(e.target.value))}>
        <option value="">keep the donor's icon</option>
        {p.fonts.map((f) => (
          <option key={f.slot} value={f.slot}>
            {`slot ${f.slot} · ${f.cell_height}px`}
          </option>
        ))}
      </select>
      <p className="hint">
        Choosing a font here blanks the caption, making this a glyph-only button.
      </p>

      <label htmlFor="gb-gesture">Gesture</label>
      <select id="gb-gesture" value={gesture}
              onChange={(e) => setGesture(e.target.value)}>
        <option value="latch">Tap toggles</option>
        <option value="hold_to_toggle">Hold 3 s to toggle</option>
      </select>

      <details className="escape-hatch">
        <summary>donor and placeholder cell</summary>
        <label htmlFor="gb-donor">Donor object</label>
        <input id="gb-donor" inputMode="numeric" value={donor}
               onChange={(e) => setDonor(e.target.value)} />
        <label htmlFor="gb-fill">Placeholder colour cell</label>
        <input id="gb-fill" inputMode="numeric" value={fillCell}
               onChange={(e) => setFillCell(e.target.value)} />
        <p className="hint">
          Wiring overwrites the placeholder cell with the pin's real status cell.
        </p>
      </details>

      <div className="card-actions">
        <button disabled={!ready} onClick={create}>create</button>
        <button onClick={p.onCancel}>cancel</button>
      </div>
    </div>
  );
}
