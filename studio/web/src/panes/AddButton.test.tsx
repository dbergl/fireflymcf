// studio/web/src/panes/AddButton.test.tsx
import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import AddButton from "./AddButton";

const pins = [
  { pin: 28, var: 11, bridged: true, colour_index: 311, commanded: true,
    dimmable: false, warnings: ["channel 28 is already commanded"] },
  { pin: 31, var: 14, bridged: true, colour_index: 314, commanded: false,
    dimmable: false, warnings: [] },
];
const colours = [{ rgb: [65, 81, 163] as [number, number, number],
                   static: [161], resting: [] },
                 { rgb: [190, 30, 45] as [number, number, number],
                   static: [4], resting: [] }];

const fonts = [{ slot: 13, cell_height: 21, glyph_count: 96 },
               { slot: 15, cell_height: 26, glyph_count: 96 }];

const props = (over: any = {}) => ({
  pins, colours, fonts, onCreate: vi.fn(), onCancel: vi.fn(), ...over,
});

describe("AddButton", () => {
  it("emits add_button with label, channel and off colour", async () => {
    const onCreate = vi.fn();
    render(<AddButton {...props({ onCreate })} />);
    await userEvent.type(screen.getByLabelText(/caption/i), "Overflow Valve");
    await userEvent.selectOptions(screen.getByLabelText(/pin/i), "31");
    await userEvent.click(screen.getByRole("button", { name: /create/i }));
    const [drafts] = onCreate.mock.calls[0];
    expect(drafts[0].type).toBe("add_button");
    expect(drafts[0].params.label).toBe("Overflow Valve");
    expect(drafts[0].params.channel).toBe(31);
    expect(drafts[0].params.off_colour).toHaveLength(3);
  });

  it("requires acknowledgement for a commanded pin and records it as a note",
     async () => {
    const onCreate = vi.fn();
    render(<AddButton {...props({ onCreate })} />);
    await userEvent.type(screen.getByLabelText(/caption/i), "Valve");
    await userEvent.selectOptions(screen.getByLabelText(/pin/i), "28");
    const create = screen.getByRole("button", { name: /create/i });
    expect(create).toBeDisabled();
    await userEvent.click(screen.getByLabelText(/acknowledge/i));
    await userEvent.click(create);
    const [drafts] = onCreate.mock.calls[0];
    expect(drafts[0].params.allow_commanded_channel).toBe(true);
    expect(drafts[0].note).toMatch(/already commanded/i);
  });

  it("adds a set_gesture draft when hold-to-toggle is chosen", async () => {
    const onCreate = vi.fn();
    render(<AddButton {...props({ onCreate })} />);
    await userEvent.type(screen.getByLabelText(/caption/i), "Starlink");
    await userEvent.selectOptions(screen.getByLabelText(/pin/i), "31");
    await userEvent.selectOptions(screen.getByLabelText(/gesture/i), "hold_to_toggle");
    await userEvent.click(screen.getByRole("button", { name: /create/i }));
    const [drafts] = onCreate.mock.calls[0];
    expect(drafts).toHaveLength(2);
    expect(drafts[1].type).toBe("set_gesture");
    expect(drafts[1].params.kind).toBe("hold_to_toggle");
  });

  it("cannot create without a caption", async () => {
    render(<AddButton {...props()} />);
    await userEvent.selectOptions(screen.getByLabelText(/pin/i), "31");
    expect(screen.getByRole("button", { name: /create/i })).toBeDisabled();
  });

  // FINDING 6: bind_pin refuses every unwatched pin before any consent gate runs
  // (BIND_REFUSED, no offers) -- an "other channels" optgroup here can never
  // succeed, so it must not be offered at all. The Inspector's remap picker is a
  // different op (remap_channel, on a control that already exists) and keeps it.
  it("never shows an unwatched-channels group -- add_button can't wire one", () => {
    render(<AddButton {...props()} />);
    expect(screen.queryByRole("group", { name: /no status indicator/i })).toBeNull();
    expect(screen.getAllByRole("group")).toHaveLength(1);   // just "pins with a status indicator"
  });
});

describe("AddButton — off colour", () => {
  it("defaults to button blue and carries a chosen colour into the op", async () => {
    const onCreate = vi.fn();
    render(<AddButton {...props({ onCreate })} />);
    await userEvent.type(screen.getByLabelText(/caption/i), "Valve");
    await userEvent.selectOptions(screen.getByLabelText(/pin/i), "31");
    // the picker is seeded from /api/reference/colors and starts on button blue
    expect(screen.getByLabelText(/off colour/i)).toHaveValue("65,81,163");
    await userEvent.selectOptions(screen.getByLabelText(/off colour/i), "190,30,45");
    await userEvent.click(screen.getByRole("button", { name: /create/i }));
    expect(onCreate.mock.calls[0][0][0].params.off_colour).toEqual([190, 30, 45]);
  });
});
