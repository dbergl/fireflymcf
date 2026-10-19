// studio/web/src/panes/CoachConfig.test.tsx
import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import CoachConfig from "./CoachConfig";

const config = {
  floorplans: [
    { value: 7, code: "WA" },
    { value: 8, code: "WD" },
    { value: 11, code: "4NJ" },   // no defaults handler on 13.6
  ],
  options: [
    { name: "Bath Fan", label: "Bath Fan", slot: 109, value: 1, page: 46 },
    { name: "Black Tank", label: "Black Tank", slot: 112, value: 1, page: 46 },
    { name: "AGS", label: "AGS", slot: 37, value: 1, page: 47 },
    { name: "AES", label: "AES", slot: 37, value: 2, page: 47 },
    { name: "AES Single Output", label: "AES Single Output", slot: 37, value: 3, page: 47 },
  ],
  defaults: {
    "7": { "Bath Fan": true, "Black Tank": false, AGS: true },
    "8": { "Bath Fan": false, "Black Tank": true, "AES Single Output": true },
  },
};

const withGroups = {
  ...config,
  slot_groups: [{
    gate_slot: 512, slot: 599, object: 1235, label: "Batteries",
    page: "options-electrical",
    choices: [{ value: 0, label: "1" }, { value: 1, label: "2" }],
  }],
};

const props = (over: any = {}) => ({
  config, preview: {}, onChange: vi.fn(), ...over,
});

describe("CoachConfig", () => {
  it("loads that floorplan's defaults when a floorplan is picked", async () => {
    const onChange = vi.fn();
    render(<CoachConfig {...props({ onChange })} />);
    await userEvent.selectOptions(screen.getByLabelText(/floorplan/i), "WD");
    expect(onChange).toHaveBeenCalledWith({
      floorplan: "WD", options: ["Black Tank", "AES Single Output"],
    });
  });

  it("replaces the previous selection rather than merging into it", async () => {
    const onChange = vi.fn();
    render(<CoachConfig {...props({
      onChange, preview: { floorplan: "WA", options: ["Bath Fan", "AGS"] } })} />);
    await userEvent.selectOptions(screen.getByLabelText(/floorplan/i), "WD");
    const [next] = onChange.mock.calls[0];
    expect(next.options).not.toContain("Bath Fan");
    expect(next.options).not.toContain("AGS");
  });

  it("lets an option be toggled on top of the loaded defaults", async () => {
    const onChange = vi.fn();
    render(<CoachConfig {...props({
      onChange, preview: { floorplan: "WD", options: ["Black Tank"] } })} />);
    await userEvent.click(screen.getByRole("checkbox", { name: /bath fan/i }));
    expect(onChange).toHaveBeenCalledWith({
      floorplan: "WD", options: ["Black Tank", "Bath Fan"],
    });
  });

  it("renders the generator options as one radio group, not three checkboxes", async () => {
    // three checkboxes would let AGS and AES be on at once; they share slot 37 and the
    // firmware distinguishes them by the VALUE written, so only one can be selected.
    render(<CoachConfig {...props({ preview: { floorplan: "WD", options: ["AES"] } })} />);
    expect(screen.queryByRole("checkbox", { name: /^AGS$/i })).toBeNull();
    const radios = screen.getAllByRole("radio");
    expect(radios.map((r) => (r as HTMLInputElement).value)).toEqual(
      expect.arrayContaining(["AGS", "AES", "AES Single Output"]));
  });

  it("selecting one generator mode deselects the others", async () => {
    const onChange = vi.fn();
    render(<CoachConfig {...props({
      onChange, preview: { floorplan: "WD", options: ["Black Tank", "AGS"] } })} />);
    await userEvent.click(screen.getByRole("radio", { name: /AES Single Output/i }));
    const [next] = onChange.mock.calls[0];
    expect(next.options).toContain("AES Single Output");
    expect(next.options).not.toContain("AGS");
    expect(next.options).toContain("Black Tank");
  });

  it("can clear a generator mode back to none", async () => {
    // slot 37 = 0 is a real state (SY ships with no generator mode); without a None
    // choice a radio group is a one-way door.
    const onChange = vi.fn();
    render(<CoachConfig {...props({
      onChange, preview: { floorplan: "WD", options: ["Black Tank", "AGS"] } })} />);
    await userEvent.click(screen.getByRole("radio", { name: /none/i }));
    const [next] = onChange.mock.calls[0];
    expect(next.options).toEqual(["Black Tank"]);
  });

  it("leaves the options alone for a floorplan with no defaults", async () => {
    const onChange = vi.fn();
    render(<CoachConfig {...props({
      onChange, preview: { floorplan: "WD", options: ["Black Tank"] } })} />);
    await userEvent.selectOptions(screen.getByLabelText(/floorplan/i), "4NJ");
    expect(onChange).toHaveBeenCalledWith({
      floorplan: "4NJ", options: ["Black Tank"],
    });
  });

  it("says so when the selected floorplan has no defaults", () => {
    // controlled component: the note follows the preview prop, not the click
    render(<CoachConfig {...props({ preview: { floorplan: "4NJ", options: [] } })} />);
    expect(screen.getByText(/no stored defaults/i)).toBeTruthy();
  });

  it("shows no such note for a floorplan that has defaults", () => {
    render(<CoachConfig {...props({ preview: { floorplan: "WD", options: [] } })} />);
    expect(screen.queryByText(/no stored defaults/i)).toBeNull();
  });

  it("shows a control for each derived slot group, defaulting to the first choice", () => {
    // slot 599 unset means 0, which is the '1' choice -- proven on the coach
    render(<CoachConfig {...props({ config: withGroups, preview: { floorplan: "WD" } })} />);
    const radios = screen.getAllByRole("radio", { name: /^[12]$/ });
    expect(radios).toHaveLength(2);
    expect((radios[0] as HTMLInputElement).checked).toBe(true);
  });

  it("writes the chosen slot value into extra_slots", async () => {
    const onChange = vi.fn();
    render(<CoachConfig {...props({
      config: withGroups, onChange, preview: { floorplan: "WD" } })} />);
    await userEvent.click(screen.getByRole("radio", { name: "2" }));
    const [next] = onChange.mock.calls[0];
    expect(next.extra_slots).toEqual({ "599": 1 });
    expect(next.floorplan).toBe("WD");
  });

  it("keeps a slot group selection when an option is toggled", async () => {
    const onChange = vi.fn();
    render(<CoachConfig {...props({
      config: withGroups, onChange,
      preview: { floorplan: "WD", options: [], extra_slots: { "599": 1 } } })} />);
    await userEvent.click(screen.getByRole("checkbox", { name: /bath fan/i }));
    const [next] = onChange.mock.calls[0];
    expect(next.extra_slots).toEqual({ "599": 1 });
  });
});

describe("CoachConfig — what a setting also drives", () => {
  it("says the generator mode picks the battery block, beside that control", () => {
    // The panel DERIVES the HOME battery block from this setting rather than receiving
    // it, so there is no control for it and never should be. Saying so where the cause
    // is stops the hunt -- reaching that block used to mean the raw-slots escape hatch.
    render(<CoachConfig {...props({
      config: { ...config,
                derives: [{ source_slot: 37, what: "the HOME battery block",
                            detail: "The panel picks the battery display from this "
                                    + "setting, so it is not a separate choice." }] },
    })} />);
    expect(screen.getByText(/Also sets the HOME battery block/)).toBeInTheDocument();
  });

  it("puts a checkbox's derivation beside the checkbox", () => {
    // The nav rail is derived from three settings, two of which are pickable here: the
    // generator radio and the Progressive Inverter CHECKBOX. Only the radios carried
    // the note, so the inverter's half of the coupling was invisible -- and it is the
    // half that explains an Electrical icon surviving a generator mode of none.
    render(<CoachConfig {...props({
      config: { ...config,
                options: [...config.options,
                          { name: "Progressive Inverter", label: "Progressive Inverter",
                            slot: 113, value: 1, page: 47 }],
                derives: [{ source_slot: 113, what: "the left icon column",
                            detail: "The Electrical icon shows only when the coach has "
                                    + "something electrical fitted." }] },
    })} />);
    expect(screen.getByText(/Also sets the left icon column/)).toBeInTheDocument();
  });

  it("says nothing when the server reports no derivation", () => {
    render(<CoachConfig {...props()} />);
    expect(screen.queryByText(/Also sets/)).toBeNull();
  });
});

describe("CoachConfig — a control no page places", () => {
  const unplaced = {
    ...config,
    slot_groups: [
      { gate_slot: 512, slot: 599, object: 1235, label: "Batteries",
        page: "options-electrical",
        choices: [{ value: 0, label: "1" }, { value: 1, label: "2" }] },
      // 13.6's 'Battery': placed by no root in the whole object table.
      { gate_slot: 512, slot: 509, object: 1121, label: "Battery", page: null,
        choices: [{ value: 0, label: "1" }, { value: 1, label: "2" }] },
    ],
  };

  it("says where a placed control is shown", () => {
    render(<CoachConfig {...props({ config: unplaced })} />);
    expect(screen.getByText(/Shown on the options-electrical page/))
      .toBeInTheDocument();
  });

  it("disables one no page places, and says nothing here changes", () => {
    render(<CoachConfig {...props({ config: unplaced })} />);
    expect(screen.getByText(/Not on any page the preview can draw/))
      .toBeInTheDocument();
    const group = screen.getByText("Battery").closest("fieldset")!;
    for (const radio of Array.from(group.querySelectorAll("input"))) {
      expect(radio).toBeDisabled();
    }
  });

  it("leaves the placed group's radios usable", () => {
    render(<CoachConfig {...props({ config: unplaced })} />);
    const group = screen.getByText("Batteries").closest("fieldset")!;
    for (const radio of Array.from(group.querySelectorAll("input"))) {
      expect(radio).toBeEnabled();
    }
  });

  // A build with no decodable floorplan table (11.23) sends floorplans: []. The picker must
  // render -- just "— none —" and no floorplan choices -- while the options below still work.
  it("renders with no floorplans (a build with no floorplan table)", () => {
    const noFloorplans = { ...config, floorplans: [], defaults: {} };
    render(<CoachConfig {...props({ config: noFloorplans })} />);
    const select = screen.getByRole("combobox");
    const opts = Array.from(select.querySelectorAll("option")).map((o) => o.textContent);
    expect(opts).toEqual(["— none —"]);              // no floorplan choices, no crash
    expect(screen.getByText("Bath Fan")).toBeInTheDocument();   // options still render
  });
});
