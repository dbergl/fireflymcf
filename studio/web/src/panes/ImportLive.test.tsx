// studio/web/src/panes/ImportLive.test.tsx
import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import ImportLive from "./ImportLive";

const result = {
  preview: { floorplan: "WD", options: ["Bunk Accent"], extra_slots: { "599": 0 } },
  diff: [
    { field: "floorplan", from: "SY", to: "WD" },
    { field: "options", from: ["Bath Fan"], to: ["Bunk Accent"] },
  ],
  frames: 2063,
  source: "9C",
  message: "2 change(s) from the coach",
};

const props = (over: any = {}) => ({
  onImport: vi.fn().mockResolvedValue(result), onApply: vi.fn(), ...over,
});

const paste = async (text = "(1.0) can1 195FCE9C#F5000800000000") => {
  await userEvent.type(screen.getByLabelText(/paste capture/i), text);
  await userEvent.click(screen.getByRole("button", { name: /read/i }));
};

describe("ImportLive", () => {
  it("is collapsed and applies nothing until asked", () => {
    const onApply = vi.fn();
    render(<ImportLive {...props({ onApply })} />);
    expect(screen.queryByRole("table")).toBeNull();
    expect(onApply).not.toHaveBeenCalled();
  });

  it("shows the diff and does NOT apply it yet", async () => {
    const onApply = vi.fn();
    render(<ImportLive {...props({ onApply })} />);
    await paste();
    expect(screen.getByText(/SY/)).toBeTruthy();
    expect(screen.getByText(/WD/)).toBeTruthy();
    expect(onApply).not.toHaveBeenCalled();
  });

  it("applies the whole proposed preview only when confirmed", async () => {
    const onApply = vi.fn();
    render(<ImportLive {...props({ onApply })} />);
    await paste();
    await userEvent.click(screen.getByRole("button", { name: /apply/i }));
    expect(onApply).toHaveBeenCalledWith(result.preview);
  });

  it("discards the proposal on cancel", async () => {
    const onApply = vi.fn();
    render(<ImportLive {...props({ onApply })} />);
    await paste();
    await userEvent.click(screen.getByRole("button", { name: /cancel/i }));
    expect(onApply).not.toHaveBeenCalled();
    expect(screen.queryByRole("table")).toBeNull();
  });

  it("says so quietly when a capture has no status frames", async () => {
    const empty = { preview: {}, diff: [], frames: 0, source: null,
                    message: "no 0x15FCE configuration-status frames in that capture" };
    const onApply = vi.fn();
    render(<ImportLive {...props({
      onImport: vi.fn().mockResolvedValue(empty), onApply })} />);
    await paste("nonsense");
    expect(screen.getByText(/no 0x15FCE/i)).toBeTruthy();
    expect(screen.queryByRole("button", { name: /apply/i })).toBeNull();
    expect(onApply).not.toHaveBeenCalled();
  });

  it("reports an already-matching coach without offering to apply", async () => {
    const same = { preview: { floorplan: "WD" }, diff: [], frames: 900, source: "9C",
                   message: "nothing to change -- the project already matches the coach" };
    render(<ImportLive {...props({ onImport: vi.fn().mockResolvedValue(same) })} />);
    await paste();
    expect(screen.getByText(/already matches/i)).toBeTruthy();
    expect(screen.queryByRole("button", { name: /apply/i })).toBeNull();
  });

  it("surfaces a failure without breaking the picker", async () => {
    render(<ImportLive {...props({
      onImport: vi.fn().mockRejectedValue(new Error("boom")) })} />);
    await paste();
    expect(screen.getByText(/boom|could not/i)).toBeTruthy();
  });
});
