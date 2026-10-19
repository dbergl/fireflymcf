import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import OpForm from "./OpForm";
import type { OpSchema } from "../api";

const moveAttach: OpSchema = {
  doc: "Move a control's attach point",
  hardware_verified: true,
  params: {
    properties: {
      page: { type: ["string", "integer"], description: "page name or index" },
      child: { type: ["integer", "object"], description: "object index" },
      x: { type: ["integer", "null"], default: null, description: "new x" },
      y: { type: ["integer", "null"], default: null, description: "new y" },
    },
    required: ["page", "child"],
  },
};

const noParams: OpSchema = {
  doc: "Widen the tank-heater whitelists",
  hardware_verified: true,
  params: { properties: {}, required: [] },
};

const unverified: OpSchema = {
  doc: "Park a control off-canvas", hardware_verified: false,
  params: { properties: { object: { type: "integer" } }, required: ["object"] },
};

const props = (over: any = {}) => ({
  type: "move_attach", schema: moveAttach,
  onCreate: vi.fn(), onCancel: vi.fn(), ...over,
});

describe("OpForm", () => {
  it("names the op and shows its documentation line", () => {
    render(<OpForm {...props()} />);
    expect(screen.getByText("move_attach")).toBeInTheDocument();
    expect(screen.getByText(/move a control's attach point/i)).toBeInTheDocument();
  });

  it("renders one control per schema property, with its description", () => {
    render(<OpForm {...props()} />);
    for (const name of ["page", "child", "x", "y"]) {
      expect(screen.getByLabelText(new RegExp(`^${name}`, "i"))).toBeInTheDocument();
    }
    expect(screen.getByText(/page name or index/i)).toBeInTheDocument();
  });

  it("keeps create disabled until every required field is filled", async () => {
    render(<OpForm {...props()} />);
    const create = screen.getByRole("button", { name: /add this change/i });
    expect(create).toBeDisabled();
    await userEvent.type(screen.getByLabelText(/^page/i), "home");
    expect(create).toBeDisabled();
    await userEvent.type(screen.getByLabelText(/^child/i), "42");
    expect(create).toBeEnabled();
  });

  it("emits the op with coerced params, omitting untouched optionals", async () => {
    const onCreate = vi.fn();
    render(<OpForm {...props({ onCreate })} />);
    await userEvent.type(screen.getByLabelText(/^page/i), "home");
    await userEvent.type(screen.getByLabelText(/^child/i), "42");
    await userEvent.type(screen.getByLabelText(/^y/i), "388");
    await userEvent.click(screen.getByRole("button", { name: /add this change/i }));
    expect(onCreate).toHaveBeenCalledWith({
      type: "move_attach", params: { page: "home", child: 42, y: 388 },
    });
  });

  it("can create a no-parameter op immediately", async () => {
    const onCreate = vi.fn();
    render(<OpForm {...props({ type: "tank_heater_names", schema: noParams,
                               onCreate })} />);
    const create = screen.getByRole("button", { name: /add this change/i });
    expect(create).toBeEnabled();
    await userEvent.click(create);
    expect(onCreate).toHaveBeenCalledWith({ type: "tank_heater_names", params: {} });
  });

  it("warns when the op's mechanism has never been flashed", () => {
    render(<OpForm {...props({ type: "remove_control", schema: unverified })} />);
    expect(screen.getByText(/never been flashed/i)).toBeInTheDocument();
  });

  it("says plainly that the engine is the authority on what it will accept", () => {
    render(<OpForm {...props()} />);
    expect(screen.getByText(/refuses it/i)).toBeInTheDocument();
  });

  it("cancels without emitting", async () => {
    const onCreate = vi.fn();
    const onCancel = vi.fn();
    render(<OpForm {...props({ onCreate, onCancel })} />);
    await userEvent.click(screen.getByRole("button", { name: /cancel/i }));
    expect(onCancel).toHaveBeenCalled();
    expect(onCreate).not.toHaveBeenCalled();
  });
});
