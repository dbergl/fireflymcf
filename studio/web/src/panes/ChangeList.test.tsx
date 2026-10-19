import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import ChangeList from "./ChangeList";
import type { OpSpec } from "../api";

const ops: OpSpec[] = [
  { id: "a", type: "rename_label", params: { object: 35, new: "28 - Valve" },
    enabled: true, note: "" },
  { id: "b", type: "add_button", params: { channel: 28, label: "Valve" },
    enabled: false, note: "group 94" },
];

describe("ChangeList", () => {
  it("lists every op with its type and enabled state", () => {
    render(<ChangeList ops={ops} errorsById={{}} onReorder={vi.fn()} onToggle={vi.fn()}
                       onRemove={vi.fn()} onNote={vi.fn()} onAdd={vi.fn()}
                       onExport={vi.fn()} />);
    expect(screen.getByText(/rename_label/)).toBeInTheDocument();
    expect(screen.getByText(/add_button/)).toBeInTheDocument();
    const boxes = screen.getAllByRole("checkbox");
    expect((boxes[0] as HTMLInputElement).checked).toBe(true);
    expect((boxes[1] as HTMLInputElement).checked).toBe(false);
  });

  it("toggling an op calls onToggle with its id", async () => {
    const onToggle = vi.fn();
    render(<ChangeList ops={ops} errorsById={{}} onReorder={vi.fn()} onToggle={onToggle}
                       onRemove={vi.fn()} onNote={vi.fn()} onAdd={vi.fn()}
                       onExport={vi.fn()} />);
    await userEvent.click(screen.getAllByRole("checkbox")[1]);
    expect(onToggle).toHaveBeenCalledWith("b");
  });

  it("shows a failed op's code and its offers as buttons", () => {
    const errorsById = { a: { op_id: "a", code: "LABEL_TOO_LONG",
                              message: "does not fit", offers: ["grow", "shorten"] } };
    render(<ChangeList ops={ops} errorsById={errorsById} onReorder={vi.fn()}
                       onToggle={vi.fn()} onRemove={vi.fn()} onNote={vi.fn()}
                       onAdd={vi.fn()} onExport={vi.fn()} />);
    expect(screen.getByText("LABEL_TOO_LONG")).toBeInTheDocument();
    expect(screen.getByText("does not fit")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /grow/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /shorten/i })).toBeInTheDocument();
  });

  it("renders an op's note", () => {
    render(<ChangeList ops={ops} errorsById={{}} onReorder={vi.fn()} onToggle={vi.fn()}
                       onRemove={vi.fn()} onNote={vi.fn()} onAdd={vi.fn()}
                       onExport={vi.fn()} />);
    expect(screen.getByDisplayValue("group 94")).toBeInTheDocument();
  });

  it("remove calls onRemove with the id", async () => {
    const onRemove = vi.fn();
    render(<ChangeList ops={ops} errorsById={{}} onReorder={vi.fn()} onToggle={vi.fn()}
                       onRemove={onRemove} onNote={vi.fn()} onAdd={vi.fn()}
                       onExport={vi.fn()} />);
    await userEvent.click(screen.getAllByRole("button", { name: /remove/i })[0]);
    expect(onRemove).toHaveBeenCalledWith("a");
  });

  it("disables checkboxes, remove buttons and notes while a save is in flight", () => {
    // Finding 2: every handler builds `next` off the query-cache ops, which only
    // update after save -> invalidate -> refetch; a second edit inside that window
    // would silently overwrite the first, so controls must be inert meanwhile.
    render(<ChangeList ops={ops} errorsById={{}} onReorder={vi.fn()} onToggle={vi.fn()}
                       onRemove={vi.fn()} onNote={vi.fn()} onAdd={vi.fn()}
                       onExport={vi.fn()} disabled />);
    const boxes = screen.getAllByRole("checkbox");
    expect((boxes[0] as HTMLInputElement).disabled).toBe(true);
    const removeButtons = screen.getAllByRole("button", { name: /remove/i });
    expect((removeButtons[0] as HTMLButtonElement).disabled).toBe(true);
    const notes = screen.getAllByPlaceholderText("note…");
    expect((notes[0] as HTMLInputElement).disabled).toBe(true);
  });

  // FINDING 7: the note field used to call onNote on every keystroke (and disable
  // itself while that save was in flight, dropping focus mid-word). It must now
  // buffer locally and commit once, on blur.
  it("commits the note on blur, not on every keystroke", async () => {
    const onNote = vi.fn();
    render(<ChangeList ops={ops} errorsById={{}} onReorder={vi.fn()} onToggle={vi.fn()}
                       onRemove={vi.fn()} onNote={onNote} onAdd={vi.fn()}
                       onExport={vi.fn()} />);
    const note = screen.getAllByPlaceholderText("note…")[0];
    await userEvent.type(note, "hello");
    expect(onNote).not.toHaveBeenCalled();
    await userEvent.tab();   // blur
    expect(onNote).toHaveBeenCalledTimes(1);
    expect(onNote).toHaveBeenCalledWith("a", "hello");
  });

  it("does not commit on blur when the note is unchanged", async () => {
    const onNote = vi.fn();
    render(<ChangeList ops={ops} errorsById={{}} onReorder={vi.fn()} onToggle={vi.fn()}
                       onRemove={vi.fn()} onNote={onNote} onAdd={vi.fn()}
                       onExport={vi.fn()} />);
    const note = screen.getAllByPlaceholderText("note…")[1];   // op "b", note "group 94"
    note.focus();
    await userEvent.tab();
    expect(onNote).not.toHaveBeenCalled();
  });

  // FINDING 4: every other ChangeList control already respected `disabled` via the
  // native attribute; drag-reorder was the one that didn't, since a native drag
  // ignores it. Simulate the drop directly (jsdom has no real drag-and-drop).
  it("ignores a drop (drag-reorder) while disabled", () => {
    const onReorder = vi.fn();
    const { container } = render(
      <ChangeList ops={ops} errorsById={{}} onReorder={onReorder} onToggle={vi.fn()}
                 onRemove={vi.fn()} onNote={vi.fn()} onAdd={vi.fn()} onExport={vi.fn()}
                 disabled />);
    const items = container.querySelectorAll("li");
    const dataTransfer = { getData: () => "0", setData: () => {} };
    fireEvent.drop(items[1], { dataTransfer });
    expect(onReorder).not.toHaveBeenCalled();
  });

  it("reorders via drop when not disabled", () => {
    const onReorder = vi.fn();
    const { container } = render(
      <ChangeList ops={ops} errorsById={{}} onReorder={onReorder} onToggle={vi.fn()}
                 onRemove={vi.fn()} onNote={vi.fn()} onAdd={vi.fn()} onExport={vi.fn()} />);
    const items = container.querySelectorAll("li");
    const dataTransfer = { getData: () => "0", setData: () => {} };
    fireEvent.drop(items[1], { dataTransfer });
    expect(onReorder).toHaveBeenCalledWith(0, 1);
  });

  // FINDING 2: an offer button (e.g. "grow" on a LABEL_TOO_LONG error) must reach
  // onOffer with the op's id and the offer's name -- it was never wired at all.
  it("clicking an offer calls onOffer with the op id and the offer name", async () => {
    const onOffer = vi.fn();
    const errorsById = { a: { op_id: "a", code: "LABEL_TOO_LONG",
                              message: "does not fit", offers: ["grow", "shorten"] } };
    render(<ChangeList ops={ops} errorsById={errorsById} onReorder={vi.fn()}
                       onToggle={vi.fn()} onRemove={vi.fn()} onNote={vi.fn()}
                       onAdd={vi.fn()} onExport={vi.fn()} onOffer={onOffer} />);
    await userEvent.click(screen.getByRole("button", { name: /^grow$/i }));
    expect(onOffer).toHaveBeenCalledWith("a", "grow");
  });
});
