// studio/web/src/panes/ImportGlyph.test.tsx
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import ImportGlyph from "./ImportGlyph";

const fonts = [{ slot: 27, cell_height: 128, glyph_count: 36 },
               { slot: 13, cell_height: 21, glyph_count: 224 }];

const props = (over: any = {}) => ({
  fonts, onCreate: vi.fn(), onCancel: vi.fn(), ...over,
});

// jsdom has neither createImageBitmap nor a real canvas, so both are stubbed with the
// smallest thing that still exercises the component's own logic: the alpha channel it
// reads and the size it derives the bearing from. The quantisation rule itself is
// tested against raw RGBA in glyphimport.test.ts, without any DOM at all.
const stubImage = (width: number, height: number, alphas: number[]) => {
  (globalThis as any).createImageBitmap = vi.fn(async () => ({ width, height }));
  const data = new Uint8ClampedArray(width * height * 4);
  alphas.forEach((a, i) => { data[i * 4 + 3] = a; });
  vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue({
    drawImage: vi.fn(),
    getImageData: () => ({ data, width, height }),
  } as any);
};

const upload = async () => {
  const file = new File(["x"], "starlink.png", { type: "image/png" });
  await userEvent.upload(screen.getByLabelText("glyph artwork"), file);
};

describe("ImportGlyph", () => {
  beforeEach(() => vi.restoreAllMocks());

  it("turns artwork into install_glyph rows, with the bearing it computed", async () => {
    stubImage(2, 2, [0, 255, 136, 17]);
    const onCreate = vi.fn();
    render(<ImportGlyph {...props({ onCreate })} />);
    await upload();
    await screen.findByText(/2×2 px/);
    await userEvent.selectOptions(screen.getByLabelText(/font slot/i), "27");
    await userEvent.type(screen.getByLabelText(/character to claim/i), "E");
    await userEvent.click(screen.getByRole("button", { name: /add this change/i }));

    expect(onCreate).toHaveBeenCalledWith(expect.objectContaining({
      type: "install_glyph",
      // (128 - 2) / 2 = 63: the convention the engine enforces, never typed by hand.
      params: { slot: 27, char: "E", y: 63, rows: ["0f", "81"] },
    }));
  });

  it("cannot add the change until artwork, slot and character are all present", async () => {
    stubImage(1, 1, [255]);
    render(<ImportGlyph {...props()} />);
    const add = () => screen.getByRole("button", { name: /add this change/i });
    expect(add()).toBeDisabled();
    await upload();
    expect(add()).toBeDisabled();                        // no slot yet
    await userEvent.selectOptions(screen.getByLabelText(/font slot/i), "27");
    expect(add()).toBeDisabled();                        // no character yet
    await userEvent.type(screen.getByLabelText(/character to claim/i), "E");
    expect(add()).toBeEnabled();
  });

  it("shows the decoded coverage before it becomes an op", async () => {
    stubImage(2, 2, [0, 255, 255, 0]);
    render(<ImportGlyph {...props()} />);
    await upload();
    expect(await screen.findByLabelText("glyph preview")).toBeInTheDocument();
  });

  it("reports a file it cannot decode instead of adding an empty glyph", async () => {
    (globalThis as any).createImageBitmap = vi.fn(async () => {
      throw new Error("not an image");
    });
    const onCreate = vi.fn();
    render(<ImportGlyph {...props({ onCreate })} />);
    await upload();
    expect(await screen.findByText(/not an image/)).toBeInTheDocument();
    expect(onCreate).not.toHaveBeenCalled();
  });

  // ---- copying a glyph that already exists -------------------------------------------
  //
  // The copy path exists because pixels are not the whole glyph: the 10 kerning bytes and
  // the +0x0e flag bit cannot be derived from a bitmap (GUICODE.md Finding 46). An import
  // that dropped them would look identical and measure differently, so these tests pin
  // that they survive the round trip through the card.

  const exported = {
    slot: 27, char: "A", x: 0, y: 37,
    rows: ["0f", "f0"],
    profiles: [1, 2, 3, 4, 5, 6, 7, 8, 9, 10],
    flag: 1,
  };

  // This jsdom's File has no `.text()` (a Blob method every target browser has had since
  // 2019, and the one the e2e run in real Chromium exercises), so it is supplied here --
  // the same shape of gap as the createImageBitmap stub above, and stubbed the same way
  // rather than making the component carry a FileReader fallback nothing needs.
  const uploadExport = async (doc: unknown, name = "glyph.json") => {
    const body = JSON.stringify(doc);
    const file = new File([body], name, { type: "application/json" });
    if (typeof file.text !== "function") {
      Object.defineProperty(file, "text", { value: async () => body });
    }
    await userEvent.upload(screen.getByLabelText("exported glyph"), file);
  };

  it("carries the source glyph's kerning bytes, bearing and flag into the op", async () => {
    const onCreate = vi.fn();
    render(<ImportGlyph {...props({ onCreate })} />);
    await uploadExport(exported);
    await screen.findByText(/2×2 px/);
    await userEvent.clear(screen.getByLabelText(/character to claim/i));
    await userEvent.type(screen.getByLabelText(/character to claim/i), "E");
    await userEvent.click(screen.getByRole("button", { name: /add this change/i }));

    // y is 37, NOT the (128-2)/2 = 63 the artwork path would compute -- a copied glyph
    // keeps its own bearing, and says so by setting force_y.
    expect(onCreate).toHaveBeenCalledWith({
      type: "install_glyph",
      params: { slot: 27, char: "E", y: 37, rows: ["0f", "f0"], x: 0,
                profiles: [1, 2, 3, 4, 5, 6, 7, 8, 9, 10], flag: 1, force_y: true },
    });
  });

  it("prefills the slot and character from the export, and warns about the bearing", async () => {
    render(<ImportGlyph {...props()} />);
    await uploadExport(exported);
    expect(await screen.findByLabelText(/font slot/i)).toHaveValue("27");
    expect(screen.getByLabelText(/character to claim/i)).toHaveValue("A");
    expect(screen.getByText(/deliberate override/)).toBeInTheDocument();
  });

  it("does not override the bearing when the source already matches the convention", async () => {
    const onCreate = vi.fn();
    render(<ImportGlyph {...props({ onCreate })} />);
    await uploadExport({ ...exported, y: 63 });          // (128 - 2) / 2
    await screen.findByText(/2×2 px/);
    await userEvent.clear(screen.getByLabelText(/character to claim/i));
    await userEvent.type(screen.getByLabelText(/character to claim/i), "E");
    await userEvent.click(screen.getByRole("button", { name: /add this change/i }));
    expect(onCreate.mock.calls[0][0].params).not.toHaveProperty("force_y");
  });

  it("refuses a JSON file that is not a glyph export", async () => {
    const onCreate = vi.fn();
    render(<ImportGlyph {...props({ onCreate })} />);
    await uploadExport({ id: "x", type: "rename_label", params: exported });
    expect(await screen.findByText(/not an install_glyph/)).toBeInTheDocument();
    expect(onCreate).not.toHaveBeenCalled();
  });
});
