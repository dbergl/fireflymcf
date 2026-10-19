// studio/web/src/panes/Inspector.test.tsx
import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import Inspector from "./Inspector";

const selection = {
  obj_index: 10,
  placements: [
    { obj_index: 10, wtype: 33, kind: "box", box: [100, 100, 300, 200] as any,
      label: null, font: null, editable: ["recolor", "move_element"] },
    { obj_index: 10, wtype: 11, kind: "label", box: [120, 130, 200, 160] as any,
      label: "Water Pump", font: 13, editable: ["rename_label", "set_font"] },
  ],
};
const colours = [{ rgb: [65, 81, 163] as [number, number, number],
                   static: [161], resting: [] },
                 { rgb: [190, 30, 45] as [number, number, number],
                   static: [4], resting: [] }];
const fonts = [{ slot: 13, cell_height: 21, glyph_count: 96 },
               { slot: 15, cell_height: 26, glyph_count: 96 }];

const props = (over: any = {}) => ({
  selection, binding: null, colours, fonts, pins: [], schemas: {},
  onOp: vi.fn(), onRemoveControl: vi.fn(), ...over,
});

describe("Inspector", () => {
  it("shows the selected object and its current label", () => {
    render(<Inspector {...props()} />);
    expect(screen.getByText(/object 10/i)).toBeInTheDocument();
    expect(screen.getByDisplayValue("Water Pump")).toBeInTheDocument();
  });

  it("editing the label emits a rename_label op targeting that object", async () => {
    const onOp = vi.fn();
    render(<Inspector {...props({ onOp })} />);
    const input = screen.getByLabelText(/label/i);
    await userEvent.clear(input);
    await userEvent.type(input, "Fresh Pump");
    await userEvent.click(screen.getByRole("button", { name: /apply label/i }));
    expect(onOp).toHaveBeenCalledWith(expect.objectContaining({
      type: "rename_label",
      params: expect.objectContaining({ object: 10, old: "Water Pump",
                                        new: "Fresh Pump" }),
    }));
  });

  it("offers grow when the new label is longer than the old", async () => {
    const onOp = vi.fn();
    render(<Inspector {...props({ onOp })} />);
    const input = screen.getByLabelText(/label/i);
    await userEvent.clear(input);
    await userEvent.type(input, "Fresh Water Pump Xtra");
    expect(screen.getByLabelText(/grow/i)).toBeInTheDocument();
    await userEvent.click(screen.getByLabelText(/grow/i));
    await userEvent.click(screen.getByRole("button", { name: /apply label/i }));
    expect(onOp.mock.calls[0][0].params.allow_grow).toBe(true);
  });

  it("changing the font emits set_font with the caption's selector", async () => {
    const onOp = vi.fn();
    render(<Inspector {...props({ onOp })} />);
    await userEvent.selectOptions(screen.getByLabelText(/font/i), "15");
    expect(onOp).toHaveBeenCalledWith(expect.objectContaining({
      type: "set_font",
      params: expect.objectContaining({ target: 10, slot: 15 }),
    }));
    // The selector must name the record on screen. A caption is named by its own text,
    // so an object with two of them (or a hundred rows) cannot be given the wrong one.
    expect(onOp.mock.calls[0][0].params.selector).toEqual({ label: "Water Pump" });
  });

  it("sets the font of the CLICKED row on a table object, not the first one", async () => {
    const onOp = vi.fn();
    const placements = [
      { obj_index: 35, wtype: 0, kind: "text", box: [0, 0, 30, 16] as any,
        label: "01 - ", font: 11,
        editable: ["rename_label", "set_font", "move_element"] },
      { obj_index: 35, wtype: 0, kind: "text", box: [0, 20, 30, 36] as any,
        label: "28 - ", font: 11,
        editable: ["rename_label", "set_font", "move_element"] },
    ];
    render(<Inspector {...props({
      onOp, selection: { obj_index: 35, hit: placements[1], placements } })} />);
    await userEvent.selectOptions(screen.getByLabelText(/font/i), "15");
    // A wtype-0 text row is named by its wtype and its index among its siblings, not by
    // its text: unlike a caption, a row's text is the thing most likely to be edited,
    // and a selector that goes stale the moment the row is renamed is worse than a
    // positional one. Either way it names the SECOND row -- the one that was clicked --
    // where the old hardcoded {wtype: 11, nth: 0} named a record of a different type
    // entirely.
    expect(onOp.mock.calls[0][0].params.selector).toEqual({ wtype: 0, nth: 1 });
  });

  it("choosing a colour emits recolor with a static palette index", async () => {
    const onOp = vi.fn();
    render(<Inspector {...props({ onOp })} />);
    await userEvent.selectOptions(screen.getByLabelText(/colour/i), "161");
    expect(onOp).toHaveBeenCalledWith(expect.objectContaining({
      type: "recolor",
      params: { object: 10, color: { index: 161 } },
    }));
  });

  it("says so when nothing is selected", () => {
    render(<Inspector {...props({ selection: null })} />);
    expect(screen.getByText(/nothing selected/i)).toBeInTheDocument();
  });

  it("does not carry grow consent across a shorten-then-lengthen cycle", async () => {
    const onOp = vi.fn();
    render(<Inspector {...props({ onOp })} />);
    const input = screen.getByLabelText(/label/i);

    // Lengthen past the field, tick grow.
    await userEvent.clear(input);
    await userEvent.type(input, "Fresh Water Pump Xtra");
    await userEvent.click(screen.getByLabelText(/grow/i));

    // Shorten back under the original -- checkbox disappears, consent should drop.
    await userEvent.clear(input);
    await userEvent.type(input, "Fp");

    // Lengthen again -- the checkbox must reappear UNCHECKED, not pre-checked.
    await userEvent.clear(input);
    await userEvent.type(input, "Fresh Water Pump Xtra Again");
    const grow = screen.getByLabelText(/grow/i) as HTMLInputElement;
    expect(grow.checked).toBe(false);

    await userEvent.click(screen.getByRole("button", { name: /apply label/i }));
    expect(onOp.mock.calls[0][0].params.allow_grow).toBeUndefined();
  });

  it("resets the colour picker to the placeholder when the selection changes", async () => {
    const other = {
      obj_index: 20,
      placements: [
        { obj_index: 20, wtype: 33, kind: "box", box: [0, 0, 50, 50] as any,
          label: null, font: null, editable: ["recolor"] },
      ],
    };
    const { rerender } = render(<Inspector {...props()} />);
    await userEvent.selectOptions(screen.getByLabelText(/colour/i), "161");
    expect((screen.getByLabelText(/colour/i) as HTMLSelectElement).value).toBe("161");

    rerender(<Inspector {...props({ selection: other })} />);
    const select2 = screen.getByLabelText(/colour/i) as HTMLSelectElement;
    expect(select2.value).toBe("");
  });

  it("gates the colour control on the box placement's own editable list, not any placement's", () => {
    const boxOnlyMove = {
      obj_index: 30,
      placements: [
        { obj_index: 30, wtype: 33, kind: "box", box: [0, 0, 50, 50] as any,
          label: null, font: null, editable: ["move_element"] },
        { obj_index: 30, wtype: 12, kind: "touch", box: [0, 0, 50, 50] as any,
          label: null, font: null, editable: ["recolor"] },
      ],
    };
    render(<Inspector {...props({ selection: boxOnlyMove })} />);
    expect(screen.queryByLabelText(/colour/i)).not.toBeInTheDocument();
  });

  it("shows an inherited placeholder for the font selector when no font is set", () => {
    const noFont = {
      obj_index: 40,
      placements: [
        { obj_index: 40, wtype: 11, kind: "label", box: [0, 0, 50, 50] as any,
          label: "Something", font: null, editable: ["set_font"] },
      ],
    };
    render(<Inspector {...props({ selection: noFont })} />);
    const select = screen.getByLabelText(/font/i) as HTMLSelectElement;
    expect(select.value).toBe("");
    expect(screen.getByRole("option", { name: /inherited/i })).toBeInTheDocument();
  });
});

const pins = [
  { pin: 28, var: 11, bridged: true, colour_index: 311, commanded: true,
    dimmable: false, warnings: ["channel 28 is already commanded by an existing control group; adding a button here needs an explicit acknowledgement"] },
  { pin: 31, var: 14, bridged: true, colour_index: 314, commanded: false,
    dimmable: false, warnings: [] },
];

describe("Inspector — pin", () => {
  it("lists pins and shows a commanded pin's warning", async () => {
    render(<Inspector {...props({ pins, binding: { group: 3, gesture: "latch",
                                                   channels: [26] } })} />);
    await userEvent.selectOptions(screen.getByLabelText(/pin/i), "28");
    expect(screen.getByText(/already commanded/i)).toBeInTheDocument();
  });

  it("blocks apply until the warning is acknowledged, then persists it as a note",
     async () => {
    const onOp = vi.fn();
    render(<Inspector {...props({ pins, onOp,
      binding: { group: 3, gesture: "latch", channels: [26] } })} />);
    await userEvent.selectOptions(screen.getByLabelText(/pin/i), "28");
    const apply = screen.getByRole("button", { name: /apply pin/i });
    expect(apply).toBeDisabled();
    await userEvent.click(screen.getByLabelText(/acknowledge/i));
    expect(apply).toBeEnabled();
    await userEvent.click(apply);
    const draft = onOp.mock.calls[0][0];
    expect(draft.type).toBe("remap_channel");
    expect(draft.params.channel).toBe(28);
    expect(draft.note).toMatch(/already commanded/i);
  });

  it("an unwarned pin applies without acknowledgement", async () => {
    const onOp = vi.fn();
    render(<Inspector {...props({ pins, onOp,
      binding: { group: 3, gesture: "latch", channels: [26] } })} />);
    await userEvent.selectOptions(screen.getByLabelText(/pin/i), "31");
    await userEvent.click(screen.getByRole("button", { name: /apply pin/i }));
    expect(onOp.mock.calls[0][0].params.channel).toBe(31);
  });

  // FINDING 6 (Inspector half): unlike AddButton -- where every unwatched channel is
  // BIND_REFUSED before any consent gate -- this picker feeds remap_channel on a
  // control that already exists, so the unwatched group can succeed here and stays.
  it("still lists unwatched channels -- remap_channel (unlike add_button) can wire one",
     () => {
    const channels = [
      { channel: 31, commanded: false, dimmable: false, watched: true },
      { channel: 77, commanded: true, dimmable: false, watched: false },
    ];
    render(<Inspector {...props({ channels,
      binding: { group: 3, gesture: "latch", channels: [26] } })} />);
    expect(screen.getByRole("group", { name: /no status indicator/i }))
      .toBeInTheDocument();
    expect(screen.getByRole("option", { name: /77/ })).toBeInTheDocument();
  });

  // FINDING 5: remap_channel's move_display_list trade-off used to be silently
  // dropped. The default now retargets the indicator; the checkbox opts into the
  // coach-proven status-variable move.
  it("defaults move_display_list to false, and the checkbox turns it on", async () => {
    const onOp = vi.fn();
    render(<Inspector {...props({ pins, onOp,
      binding: { group: 3, gesture: "latch", channels: [26] } })} />);
    await userEvent.selectOptions(screen.getByLabelText(/pin/i), "31");
    await userEvent.click(screen.getByRole("button", { name: /apply pin/i }));
    expect(onOp.mock.calls[0][0].params.move_display_list).toBe(false);

    onOp.mockClear();
    await userEvent.click(
      screen.getByLabelText(/move the status variable instead/i));
    await userEvent.click(screen.getByRole("button", { name: /apply pin/i }));
    expect(onOp.mock.calls[0][0].params.move_display_list).toBe(true);
  });
});

describe("Inspector — gesture", () => {
  it("describes the current gesture in plain language, never a phase mask", () => {
    render(<Inspector {...props({ binding: { group: 3, gesture: "latch",
                                             channels: [26] } })} />);
    expect(screen.getByText(/tap toggles/i)).toBeInTheDocument();
    expect(screen.queryByText(/phase/i)).toBeNull();
    expect(screen.queryByText(/0x/)).toBeNull();
  });

  it("choosing hold-to-toggle emits set_gesture with seconds", async () => {
    const onOp = vi.fn();
    render(<Inspector {...props({ onOp, binding: { group: 3, gesture: "latch",
                                                   channels: [26] } })} />);
    await userEvent.selectOptions(screen.getByLabelText(/gesture/i), "hold_to_toggle");
    expect(onOp).toHaveBeenCalledWith(expect.objectContaining({
      type: "set_gesture",
      params: expect.objectContaining({ kind: "hold_to_toggle", seconds: 3 }),
    }));
  });

  it("shows a momentary control read-only", () => {
    render(<Inspector {...props({ binding: { group: 3, gesture: "momentary",
                                             channels: [3] } })} />);
    expect(screen.getByText(/momentary while held/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/gesture/i)).toBeDisabled();
  });

  it("maps tap-or-hold to plain language and never leaks the raw kind", () => {
    render(<Inspector {...props({ binding: { group: 3, gesture: "tap-or-hold",
                                             channels: [26] } })} />);
    expect(screen.getByText(/tap toggles, hold to dim/i)).toBeInTheDocument();
    expect(screen.queryByText("tap-or-hold")).toBeNull();
  });

  it("falls back to a generic label for an unmapped gesture kind, never the raw token",
     () => {
    render(<Inspector {...props({ binding: { group: 3, gesture: "brand-new-kind",
                                             channels: [26] } })} />);
    expect(screen.getByText(/custom gesture/i)).toBeInTheDocument();
    expect(screen.queryByText("brand-new-kind")).toBeNull();
  });

  it("never offers latch as a choice -- the engine rejects it except as a no-op",
     () => {
    render(<Inspector {...props({ binding: { group: 3, gesture: "hold-only",
                                             channels: [26] } })} />);
    expect(screen.queryByRole("option", { name: /tap toggles/i })).toBeNull();
    expect(screen.getByRole("option", { name: /hold 3 s to toggle/i })).toBeInTheDocument();
  });
});

describe("Inspector — remove control", () => {
  it("emits onRemoveControl and warns the op is hardware-unverified", async () => {
    const onRemoveControl = vi.fn();
    render(<Inspector {...props({ onRemoveControl,
      schemas: { remove_control: { params: { properties: {}, required: [] },
                                   doc: "", hardware_verified: false } } })} />);
    expect(screen.getByText(/hardware-unverified/i)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /remove control/i }));
    expect(onRemoveControl).toHaveBeenCalled();
  });

  it("does not warn when the schema marks the op hardware-verified", () => {
    render(<Inspector {...props({
      schemas: { remove_control: { params: { properties: {}, required: [] },
                                   doc: "", hardware_verified: true } } })} />);
    expect(screen.getByRole("button", { name: /remove control/i })).toBeInTheDocument();
    expect(screen.queryByText(/hardware-unverified/i)).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// Wire-to-a-pin (`wire_button`) and Icon (`set_icon`) -- the two spec surfaces
// deferred out of the original plan-3 build.

// A shell: a box that advertises wire_button/set_icon, with no binding yet.
const shell = {
  obj_index: 42,
  placements: [
    { obj_index: 42, wtype: 33, kind: "box", box: [600, 380, 720, 440] as any,
      label: null, font: null, geom: { x: 0, y: 0 },
      editable: ["recolor", "move_element", "remap_channel", "wire_button",
                 "set_icon", "remove_control"] },
    { obj_index: 42, wtype: 11, kind: "label", box: [610, 400, 700, 425] as any,
      label: "E", font: 27, geom: { x: 10, y: 20 },
      editable: ["rename_label", "set_font"] },
  ],
};
const wirePins = [
  { pin: 29, var: 12, bridged: true, colour_index: 312, commanded: true,
    dimmable: false,
    warnings: ["channel 29 is already commanded by an existing control group"] },
  { pin: 31, var: 14, bridged: true, colour_index: 314, commanded: false,
    dimmable: false, warnings: [] },
];

describe("Inspector — wire to a pin", () => {
  it("offers wiring only when the object has no binding yet", () => {
    const { rerender } = render(
      <Inspector {...props({ selection: shell, pins: wirePins, binding: null })} />);
    expect(screen.getByLabelText(/wire to a pin/i)).toBeInTheDocument();
    rerender(<Inspector {...props({ selection: shell, pins: wirePins,
      binding: { group: 3, gesture: "latch", channels: [29] } })} />);
    expect(screen.queryByLabelText(/wire to a pin/i)).toBeNull();
  });

  it("says it wires the indicator AND the command, not status alone", () => {
    render(<Inspector {...props({ selection: shell, pins: wirePins })} />);
    expect(screen.getByText(/indicator and the command/i)).toBeInTheDocument();
  });

  it("emits wire_button with the chosen pin and off colour", async () => {
    const onOp = vi.fn();
    render(<Inspector {...props({ selection: shell, pins: wirePins, onOp })} />);
    await userEvent.selectOptions(screen.getByLabelText(/wire to a pin/i), "31");
    await userEvent.click(screen.getByRole("button", { name: /wire it/i }));
    expect(onOp).toHaveBeenCalledWith(expect.objectContaining({
      type: "wire_button",
      params: expect.objectContaining({ target: 42, channel: 31,
                                        off_colour: [65, 81, 163] }),
    }));
  });

  it("blocks wiring a commanded pin until acknowledged, and keeps the note",
     async () => {
    const onOp = vi.fn();
    render(<Inspector {...props({ selection: shell, pins: wirePins, onOp })} />);
    await userEvent.selectOptions(screen.getByLabelText(/wire to a pin/i), "29");
    const go = screen.getByRole("button", { name: /wire it/i });
    expect(go).toBeDisabled();
    await userEvent.click(screen.getByLabelText(/acknowledge/i));
    expect(go).toBeEnabled();
    await userEvent.click(go);
    const draft = onOp.mock.calls[0][0];
    expect(draft.params.allow_commanded_channel).toBe(true);
    expect(draft.note).toMatch(/already commanded/i);
  });

  it("carries the chosen off colour into the op", async () => {
    const onOp = vi.fn();
    render(<Inspector {...props({ selection: shell, pins: wirePins, onOp })} />);
    await userEvent.selectOptions(screen.getByLabelText(/wire to a pin/i), "31");
    await userEvent.selectOptions(screen.getByLabelText(/off colour/i), "190,30,45");
    await userEvent.click(screen.getByRole("button", { name: /wire it/i }));
    expect(onOp.mock.calls[0][0].params.off_colour).toEqual([190, 30, 45]);
  });
});

describe("Inspector — icon", () => {
  it("offers the icon controls when the box advertises set_icon", () => {
    render(<Inspector {...props({ selection: shell, fonts })} />);
    expect(screen.getByLabelText(/icon character/i)).toBeInTheDocument();
  });

  it("does not offer them for an object that never advertises set_icon", () => {
    render(<Inspector {...props()} />);   // the default selection has no set_icon
    expect(screen.queryByLabelText(/icon character/i)).toBeNull();
  });

  it("emits set_icon with the character", async () => {
    const onOp = vi.fn();
    render(<Inspector {...props({ selection: shell, fonts, onOp })} />);
    const ch = screen.getByLabelText(/icon character/i);
    await userEvent.clear(ch);
    await userEvent.type(ch, "F");
    await userEvent.click(screen.getByRole("button", { name: /apply icon/i }));
    expect(onOp).toHaveBeenCalledWith(expect.objectContaining({
      type: "set_icon",
      params: expect.objectContaining({ target: 42, char: "F" }),
    }));
  });

  it("emits the icon font slot when one is chosen", async () => {
    const onOp = vi.fn();
    render(<Inspector {...props({ selection: shell, fonts, onOp })} />);
    await userEvent.selectOptions(screen.getByLabelText(/icon font/i), "15");
    await userEvent.click(screen.getByRole("button", { name: /apply icon/i }));
    expect(onOp.mock.calls[0][0].params.slot).toBe(15);
  });

  it("can blank the caption to make a glyph-only button", async () => {
    const onOp = vi.fn();
    render(<Inspector {...props({ selection: shell, fonts, onOp })} />);
    await userEvent.click(screen.getByLabelText(/blank the caption/i));
    await userEvent.click(screen.getByRole("button", { name: /apply icon/i }));
    expect(onOp.mock.calls[0][0].params.blank_caption).toBe(true);
  });

  it("refuses to emit an op that would change nothing", () => {
    render(<Inspector {...props({ selection: shell, fonts })} />);
    expect(screen.getByRole("button", { name: /apply icon/i })).toBeDisabled();
  });
});

// A table object holds one record per row -- object 35 IS the whole G12 OUTPUTS list --
// so "the first label in the object" names the wrong row for every row but the first.
// These cover the record the click actually landed on being the one that is edited.
describe("Inspector — the clicked record, not the object's first", () => {
  const rows = (kind: string) => {
    const make = (label: string, y: number) => ({
      obj_index: 35, wtype: kind === "text" ? 0 : 11, kind,
      box: [325, y, 358, y + 16] as any, label, font: 11,
      editable: ["rename_label", "set_font", "move_element"],
      geom: { x: 65341, y: 36, w: null, h: null },
    });
    return [make("01 - ", 66), make("28 - ", 286), make("29 - ", 306)];
  };

  it("offers the clicked row's own text, not the first row's", () => {
    const placements = rows("text");
    render(<Inspector {...props({
      selection: { obj_index: 35, hit: placements[1], placements } })} />);
    expect(screen.getByLabelText(/^label$/i)).toHaveValue("28 - ");
  });

  it("renames the clicked row, so `old` is that row's pre-image", async () => {
    const onOp = vi.fn();
    const placements = rows("text");
    render(<Inspector {...props({
      selection: { obj_index: 35, hit: placements[2], placements }, onOp })} />);
    await userEvent.clear(screen.getByLabelText(/^label$/i));
    await userEvent.type(screen.getByLabelText(/^label$/i), "29 - Starlink");
    await userEvent.click(screen.getByLabelText(/grow the field/i));
    await userEvent.click(screen.getByRole("button", { name: "apply label" }));
    expect(onOp).toHaveBeenCalledWith(expect.objectContaining({
      type: "rename_label",
      params: expect.objectContaining({ object: 35, old: "29 - ",
                                        new: "29 - Starlink", allow_grow: true }),
    }));
  });

  it("still falls back to the object's caption when the click landed on its box", () => {
    // A button: the box is what was clicked, and its one caption is unambiguous.
    render(<Inspector {...props({
      selection: { ...selection, hit: selection.placements[0] } })} />);
    expect(screen.getByLabelText(/^label$/i)).toHaveValue("Water Pump");
  });
});
