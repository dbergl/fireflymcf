import { describe, it, expect, vi } from "vitest";
import React from "react";
import { render, screen, fireEvent } from "@testing-library/react";
import Canvas from "./Canvas";
import type { PlacementsDoc } from "../api";

const doc: PlacementsDoc = {
  etag: "e", page: "home", root: 4, canvas: [800, 480],
  placements: [
    { obj_index: 10, wtype: 33, kind: "box", box: [100, 100, 300, 200],
      label: null, font: null, editable: ["recolor"], geom: { x: 100, y: 100, w: 200, h: 100 } },
    { obj_index: 10, wtype: 11, kind: "label", box: [120, 130, 200, 160],
      label: "Water\nPump", font: 13, editable: ["rename_label"], geom: { x: 20, y: 30, w: null, h: null } },
  ],
  objects: { "10": { binding: { group: 3, gesture: "latch", channels: [44] } } },
};
const pages = [{ name: "home", root: 4 }, { name: "lights", root: 9 }];

describe("Canvas", () => {
  it("renders the page image with the render URL", () => {
    render(<Canvas name="p" page="home" pages={pages} placements={doc} knobs={{}}
                   onPageChange={vi.fn()} onKnobs={vi.fn()} selection={null}
                   onSelect={vi.fn()} onMove={vi.fn()} />);
    const img = screen.getByAltText(/home/i) as HTMLImageElement;
    expect(img.getAttribute("src")).toContain("/api/projects/p/render/home");
  });

  it("carries the etag into the render URL, so an edit forces a fresh <img> fetch", () => {
    // React no-ops a DOM attribute write when the new value is string-identical to the
    // old one, so an unchanging renderUrl means the <img> never re-requests after a
    // compile -- the overlay would update while the pixels stayed stale. The knobs.etag
    // App.tsx feeds in (the compile etag) must show up in the src.
    render(<Canvas name="p" page="home" pages={pages} placements={doc}
                   knobs={{ etag: "abc123" }}
                   onPageChange={vi.fn()} onKnobs={vi.fn()} selection={null}
                   onSelect={vi.fn()} onMove={vi.fn()} />);
    const img = screen.getByAltText(/home/i) as HTMLImageElement;
    expect(img.getAttribute("src")).toContain("etag=abc123");
  });

  it("draws one overlay rect per placement", () => {
    const { container } = render(
      <Canvas name="p" page="home" pages={pages} placements={doc} knobs={{}}
              onPageChange={vi.fn()} onKnobs={vi.fn()} selection={null}
              onSelect={vi.fn()} onMove={vi.fn()} />);
    expect(container.querySelectorAll("svg rect")).toHaveLength(2);
  });

  it("clicking a placement selects its object", () => {
    const onSelect = vi.fn();
    const { container } = render(
      <Canvas name="p" page="home" pages={pages} placements={doc} knobs={{}}
              onPageChange={vi.fn()} onKnobs={vi.fn()} selection={null}
              onSelect={onSelect} onMove={vi.fn()} />);
    const svg = container.querySelector("svg")!;
    // jsdom has no layout, so Canvas must read coordinates from the SVG viewBox
    // via getBoundingClientRect; stub it to the natural 800x480.
    vi.spyOn(svg, "getBoundingClientRect").mockReturnValue(
      { left: 0, top: 0, width: 800, height: 480 } as DOMRect);
    fireEvent.click(svg, { clientX: 150, clientY: 140 });
    expect(onSelect).toHaveBeenCalledWith(expect.objectContaining({ obj_index: 10 }));
  });

  it("switching pages calls onPageChange", () => {
    const onPageChange = vi.fn();
    render(<Canvas name="p" page="home" pages={pages} placements={doc} knobs={{}}
                   onPageChange={onPageChange} onKnobs={vi.fn()} selection={null}
                   onSelect={vi.fn()} onMove={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: "lights" }));
    expect(onPageChange).toHaveBeenCalledWith("lights");
  });

  it("highlights the selected object's placements", () => {
    const { container } = render(
      <Canvas name="p" page="home" pages={pages} placements={doc} knobs={{}}
              onPageChange={vi.fn()} onKnobs={vi.fn()}
              selection={{ obj_index: 10, hit: doc.placements[0],
                           placements: doc.placements }}
              onSelect={vi.fn()} onMove={vi.fn()} />);
    expect(container.querySelectorAll("svg rect.selected")).toHaveLength(2);
  });
});

describe("Canvas — drag feedback", () => {
  // objects[10] carries attach geometry, so the BOX is whole-control-draggable too.
  const attachDoc: PlacementsDoc = {
    ...doc,
    objects: { "10": { binding: { group: 3, gesture: "latch", channels: [44] },
                       attach: { x: 5, y: 7 } } },
  };
  const props = (over: Partial<React.ComponentProps<typeof Canvas>> = {}) => ({
    name: "p", page: "home", pages, placements: attachDoc, knobs: {},
    onPageChange: vi.fn(), onKnobs: vi.fn(), selection: null,
    onSelect: vi.fn(), onMove: vi.fn(), onMoveAttach: vi.fn(), ...over,
  });

  function startDrag(container: HTMLElement, which: "label" | "box") {
    const svg = container.querySelector("svg")!;
    vi.spyOn(svg, "getBoundingClientRect").mockReturnValue(
      { left: 0, top: 0, width: 800, height: 480 } as DOMRect);
    const rects = container.querySelectorAll("svg rect");
    const rect = which === "box" ? rects[0] : rects[1];
    fireEvent.mouseDown(rect, { clientX: 130, clientY: 140 });
    return svg;
  }

  it("shows a live ghost and a coordinate readout while dragging a label", () => {
    const { container } = render(<Canvas {...props()} />);
    const svg = startDrag(container, "label");
    fireEvent.mouseMove(svg, { clientX: 150, clientY: 160 });   // Δ(20, 20)
    const ghost = container.querySelector("svg .ghost");
    expect(ghost).not.toBeNull();
    expect(ghost!.getAttribute("transform")).toBe("translate(20, 20)");
    // geom (20, 30) + Δ(20, 20): the RAW coordinates the move op will write.
    expect(container.querySelector(".drag-readout")!.textContent).toContain("40, 50");
  });

  it("marks the dragged rect as the origin while a ghost is showing", () => {
    const { container } = render(<Canvas {...props()} />);
    const svg = startDrag(container, "label");
    fireEvent.mouseMove(svg, { clientX: 150, clientY: 160 });
    expect(container.querySelector("svg rect.drag-origin")).not.toBeNull();
  });

  it("Escape cancels the drag: no ghost, no move", () => {
    const onMove = vi.fn();
    const { container } = render(<Canvas {...props({ onMove })} />);
    const svg = startDrag(container, "label");
    fireEvent.mouseMove(svg, { clientX: 150, clientY: 160 });
    fireEvent.keyDown(window, { key: "Escape" });
    expect(container.querySelector("svg .ghost")).toBeNull();
    fireEvent.mouseUp(svg, { clientX: 150, clientY: 160 });
    expect(onMove).not.toHaveBeenCalled();
  });

  it("leaving the canvas cancels rather than commits", () => {
    const onMove = vi.fn();
    const { container } = render(<Canvas {...props({ onMove })} />);
    const svg = startDrag(container, "label");
    fireEvent.mouseMove(svg, { clientX: 150, clientY: 160 });
    fireEvent.mouseLeave(svg);
    expect(container.querySelector("svg .ghost")).toBeNull();
    fireEvent.mouseUp(svg, { clientX: 150, clientY: 160 });
    expect(onMove).not.toHaveBeenCalled();
  });

  it("dragging a box with attach geometry moves the whole control via onMoveAttach", () => {
    const onMove = vi.fn(), onMoveAttach = vi.fn();
    const { container } = render(<Canvas {...props({ onMove, onMoveAttach })} />);
    const svg = startDrag(container, "box");
    fireEvent.mouseMove(svg, { clientX: 150, clientY: 160 });
    // The readout shows the attach coordinates the op will write: (5, 7) + Δ(20, 20).
    expect(container.querySelector(".drag-readout")!.textContent).toContain("25, 27");
    fireEvent.mouseUp(svg, { clientX: 150, clientY: 160 });
    expect(onMoveAttach).toHaveBeenCalledWith(
      expect.objectContaining({ obj_index: 10, kind: "box" }), 20, 20);
    expect(onMove).not.toHaveBeenCalled();
  });

  it("a box with NO attach geometry is not draggable", () => {
    const onMoveAttach = vi.fn();
    const { container } = render(
      <Canvas {...props({ placements: doc, onMoveAttach })} />);
    const svg = startDrag(container, "box");
    fireEvent.mouseMove(svg, { clientX: 150, clientY: 160 });
    expect(container.querySelector("svg .ghost")).toBeNull();
    fireEvent.mouseUp(svg, { clientX: 150, clientY: 160 });
    expect(onMoveAttach).not.toHaveBeenCalled();
  });

  it("draggable rects say so, for the grab cursor", () => {
    const { container } = render(<Canvas {...props()} />);
    const rects = container.querySelectorAll("svg rect");
    expect(rects[0].classList.contains("draggable")).toBe(true);   // attached box
    expect(rects[1].classList.contains("draggable")).toBe(true);   // label with x/y
  });
});

describe("Canvas — while a compile is in flight", () => {
  const base = {
    name: "p", page: "home", pages, placements: doc, knobs: {},
    onPageChange: vi.fn(), onKnobs: vi.fn(), selection: null,
    onSelect: vi.fn(), onMove: vi.fn(),
  };

  it("says so, instead of silently swallowing clicks", () => {
    const { container, rerender } = render(<Canvas {...base} />);
    expect(container.querySelector(".stage")).toHaveAttribute("aria-busy", "false");
    expect(screen.queryByRole("status")).toBeNull();

    rerender(<Canvas {...base} busy />);
    expect(container.querySelector(".stage")).toHaveAttribute("aria-busy", "true");
    expect(screen.getByRole("status")).toHaveTextContent("updating");
  });
});
