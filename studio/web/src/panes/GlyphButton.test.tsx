// studio/web/src/panes/GlyphButton.test.tsx
import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import GlyphButton from "./GlyphButton";
import { PENDING_TARGET } from "../ops";

const pins = [
  { pin: 29, var: 12, bridged: true, colour_index: 312, commanded: true,
    dimmable: false, warnings: ["channel 29 is already commanded by an existing group"] },
  { pin: 31, var: 14, bridged: true, colour_index: 314, commanded: false,
    dimmable: false, warnings: [] },
];
const colours = [{ rgb: [65, 81, 163] as [number, number, number],
                   static: [161], resting: [] }];
const fonts = [{ slot: 27, cell_height: 128, glyph_count: 36 }];
const pages = [{ name: "home", root: 4 }, { name: "lights", root: 9 }];

const props = (over: any = {}) => ({
  pins, colours, fonts, pages, onCreate: vi.fn(), onCancel: vi.fn(), ...over,
});

const fill = async (id: string, value: string) => {
  const el = document.querySelector(`#${id}`) as HTMLInputElement;
  await userEvent.clear(el);
  await userEvent.type(el, value);
};

describe("GlyphButton", () => {
  it("ALWAYS emits the wire_button alongside the shell", async () => {
    // The engine refuses a shell that was never wired -- it still carries the donor's
    // command group -- and the only route to wire_button is selecting the object, which
    // needs a successful compile. Offering the shell alone was therefore a dead end the
    // editor could not get out of, so the two are one gesture and cannot be separated.
    const onCreate = vi.fn();
    render(<GlyphButton {...props({ onCreate })} />);
    await userEvent.selectOptions(screen.getByLabelText(/^pin$/i), "31");
    await userEvent.click(screen.getByRole("button", { name: /^create$/ }));

    const drafts = onCreate.mock.calls[0][0];
    expect(drafts.map((d: any) => d.type)).toEqual(["add_glyph_button", "wire_button"]);
    expect(drafts[1].params.target).toBe(PENDING_TARGET);
    expect(drafts[1].params.channel).toBe(31);
  });

  it("requires an acknowledgement for a commanded pin and records it as the note", async () => {
    const onCreate = vi.fn();
    render(<GlyphButton {...props({ onCreate })} />);
    await userEvent.selectOptions(screen.getByLabelText(/^pin$/i), "29");
    expect(screen.getByText(/already commanded/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /^create$/ })).toBeDisabled();

    await userEvent.click(screen.getByLabelText("acknowledge the warning"));
    await userEvent.click(screen.getByRole("button", { name: /^create$/ }));
    const drafts = onCreate.mock.calls[0][0];
    expect(drafts[1].params.allow_commanded_channel).toBe(true);
    expect(drafts[1].note).toMatch(/already commanded.*acknowledged in the editor/);
  });

  it("adds set_icon only when an icon font is chosen, and blanks the caption with it", async () => {
    const onCreate = vi.fn();
    render(<GlyphButton {...props({ onCreate })} />);
    await userEvent.selectOptions(screen.getByLabelText(/^pin$/i), "31");
    await userEvent.selectOptions(screen.getByLabelText(/icon font/i), "27");
    await userEvent.click(screen.getByRole("button", { name: /^create$/ }));
    const drafts = onCreate.mock.calls[0][0];
    const icon = drafts.find((d: any) => d.type === "set_icon");
    expect(icon.params).toMatchObject({ target: PENDING_TARGET, slot: 27,
                                        blank_caption: true });
  });

  it("adds set_gesture only for hold-to-toggle", async () => {
    const onCreate = vi.fn();
    render(<GlyphButton {...props({ onCreate })} />);
    await userEvent.selectOptions(screen.getByLabelText(/^pin$/i), "31");
    await userEvent.click(screen.getByRole("button", { name: /^create$/ }));
    expect(onCreate.mock.calls[0][0].some((d: any) => d.type === "set_gesture"))
      .toBe(false);

    onCreate.mockClear();
    await userEvent.selectOptions(screen.getByLabelText(/gesture/i), "hold_to_toggle");
    await userEvent.click(screen.getByRole("button", { name: /^create$/ }));
    const gesture = onCreate.mock.calls[0][0].find((d: any) => d.type === "set_gesture");
    expect(gesture.params).toMatchObject({ kind: "hold_to_toggle", seconds: 3 });
  });

  it("carries the geometry through to the shell", async () => {
    const onCreate = vi.fn();
    render(<GlyphButton {...props({ onCreate })} />);
    await userEvent.selectOptions(screen.getByLabelText(/^pin$/i), "31");
    await fill("gb-x", "630");
    await fill("gb-y", "388");
    await fill("gb-w", "120");
    await fill("gb-h", "60");
    await userEvent.click(screen.getByRole("button", { name: /^create$/ }));
    expect(onCreate.mock.calls[0][0][0].params).toMatchObject({
      page: "home", donor: 247, x: 630, y: 388, w: 120, h: 60, fill_cell: 164 });
  });

  it("cannot be created without a pin — an unwired shell is the thing to prevent", () => {
    render(<GlyphButton {...props()} />);
    expect(screen.getByRole("button", { name: /^create$/ })).toBeDisabled();
  });
});
