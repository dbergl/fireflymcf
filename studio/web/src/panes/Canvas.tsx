// studio/web/src/panes/Canvas.tsx
// The rendered page plus an SVG overlay built from the placements map. Both come
// from the server at the same etag, so the boxes always describe these pixels.
import { useEffect, useRef, useState } from "react";
import * as api from "../api";
import type { Knobs, Page, Placement, PlacementsDoc } from "../api";
import { selectAt } from "../selection";
import CoachConfig from "./CoachConfig";
import type { Selection } from "../selection";

type Props = {
  name: string; page: string; pages: Page[]; placements: PlacementsDoc | undefined;
  knobs: Knobs;
  // The coach config the canvas renders under. Optional so Canvas' own tests, which
  // predate it, keep constructing props without one.
  coachConfig?: api.CoachConfigData;
  preview?: api.Preview;
  onPreview?: (p: api.Preview) => void;
  onImportLive?: (capture: string) => Promise<api.LiveImport>;
  /** A compile or save is in flight, so the overlay is not interactive yet. */
  busy?: boolean;
  onPageChange: (page: string) => void;
  onKnobs: (k: Knobs) => void;
  selection: Selection | null;
  onSelect: (s: Selection | null) => void;
  onMove: (p: Placement, dx: number, dy: number) => void;
  /** A box drag: move the WHOLE control (its attach record), not one element.
      Optional so tests predating it keep constructing props without one. */
  onMoveAttach?: (p: Placement, dx: number, dy: number) => void;
};

const W = 800, H = 480;

// What a mousedown on `pl` starts, if anything: a label with raw x/y drags itself
// (move_element), a box whose object has attach coordinates drags the whole control
// (move_attach). Exported for the class the rects carry ("draggable" -> grab cursor).
export function dragModeFor(pl: Placement, doc: PlacementsDoc | undefined):
    "element" | "attach" | null {
  if (pl.kind === "label") {
    return pl.geom && pl.geom.x !== null && pl.geom.y !== null ? "element" : null;
  }
  if (pl.kind === "box") {
    const attach = doc?.objects[String(pl.obj_index)]?.attach;
    return attach && (attach.x !== undefined || attach.y !== undefined)
      ? "attach" : null;
  }
  return null;
}

type Drag = { p: Placement; mode: "element" | "attach"; x: number; y: number;
              // Present once the pointer has moved: the live delta the ghost shows.
              dx?: number; dy?: number };

export default function Canvas(p: Props) {
  const svgRef = useRef<SVGSVGElement>(null);
  const [drag, setDrag] = useState<Drag | null>(null);

  const toCanvas = (e: { clientX: number; clientY: number }) => {
    const r = svgRef.current!.getBoundingClientRect();
    return { x: ((e.clientX - r.left) / r.width) * W,
             y: ((e.clientY - r.top) / r.height) * H };
  };

  // Esc abandons a drag -- the one affordance that makes dragging safe to try.
  useEffect(() => {
    if (!drag) return;
    const cancel = (e: KeyboardEvent) => { if (e.key === "Escape") setDrag(null); };
    window.addEventListener("keydown", cancel);
    return () => window.removeEventListener("keydown", cancel);
  }, [drag]);

  // The raw coordinates the committed op would write right now -- what the readout
  // shows, so the user is watching the real numbers, not screen-space ones.
  const dragTarget = (d: Drag): { x?: number; y?: number } => {
    const dx = d.dx ?? 0, dy = d.dy ?? 0;
    if (d.mode === "element") {
      return { x: (d.p.geom!.x ?? 0) + dx, y: (d.p.geom!.y ?? 0) + dy };
    }
    const attach = p.placements?.objects[String(d.p.obj_index)]?.attach ?? {};
    return { ...(attach.x !== undefined ? { x: Math.max(0, attach.x + dx) } : {}),
             ...(attach.y !== undefined ? { y: Math.max(0, attach.y + dy) } : {}) };
  };

  return (
    <>
      <div className="pagebar">
        {p.pages.map((pg) => (
          <button key={pg.name} className={pg.name === p.page ? "on" : ""}
                  onClick={() => p.onPageChange(pg.name)}>{pg.name}</button>
        ))}
      </div>
      {/* The canvas ignores clicks until its placements have arrived, because the
          overlay and the pixels must describe the SAME compile -- keeping a stale
          overlay over fresh pixels would put the boxes somewhere the render does not
          agree with. That is correct, but it used to be invisible: a click during a
          recompile did nothing at all and said nothing, so the natural next move was
          to type into the inspector, which still held the PREVIOUS selection. Saying
          so is the whole fix. */}
      <div className={p.busy ? "stage busy" : "stage"} aria-busy={!!p.busy}
           style={{ width: W, height: H }}>
        {p.busy && <div className="stage-busy" role="status">updating…</div>}
        <img src={api.renderUrl(p.name, p.page, p.knobs)} width={W} height={H}
             alt={`${p.page} render`} />
        <svg ref={svgRef} viewBox={`0 0 ${W} ${H}`} width={W} height={H}
             className={drag ? "dragging" : ""}
             onClick={(e) => {
               if (!p.placements) return;
               const { x, y } = toCanvas(e);
               p.onSelect(selectAt(p.placements, x, y));
             }}
             onMouseMove={(e) => {
               if (!drag) return;
               const { x, y } = toCanvas(e);
               setDrag({ ...drag, dx: Math.round(x - drag.x),
                         dy: Math.round(y - drag.y) });
             }}
             onMouseLeave={() => setDrag(null)}
             onMouseUp={(e) => {
               if (!drag) return;
               const { x, y } = toCanvas(e);
               const dx = Math.round(x - drag.x), dy = Math.round(y - drag.y);
               if (dx || dy) {
                 if (drag.mode === "attach") p.onMoveAttach?.(drag.p, dx, dy);
                 else p.onMove(drag.p, dx, dy);
               }
               setDrag(null);
             }}>
          {(p.placements?.placements ?? []).map((pl, i) => {
            const on = p.selection?.obj_index === pl.obj_index;
            const mode = dragModeFor(pl, p.placements);
            const origin = drag?.dx !== undefined && drag.p === pl;
            const cls = [on ? "selected" : "", mode ? "draggable" : "",
                         origin ? "drag-origin" : ""].filter(Boolean).join(" ");
            return (
              <rect key={i} className={cls}
                    x={pl.box[0]} y={pl.box[1]}
                    width={Math.max(1, pl.box[2] - pl.box[0])}
                    height={Math.max(1, pl.box[3] - pl.box[1])}
                    onMouseDown={(e) => {
                      if (!mode) return;
                      const { x, y } = toCanvas(e);
                      setDrag({ p: pl, mode, x, y });
                    }}>
                <title>{`obj ${pl.obj_index} · ${pl.kind}${pl.label ? ` · ${pl.label.replace(/\n/g, " ")}` : ""}`}</title>
              </rect>
            );
          })}
          {/* The live ghost: the dragged rect translated by the current delta, so the
              user watches where the element is GOING, not just where it was. Rendered
              only once the pointer has actually moved. */}
          {drag && drag.dx !== undefined && (() => {
            const { p: pl, dx = 0, dy = 0 } = drag;
            return (
              <g className="ghost" transform={`translate(${dx}, ${dy})`}>
                <rect x={pl.box[0]} y={pl.box[1]}
                      width={Math.max(1, pl.box[2] - pl.box[0])}
                      height={Math.max(1, pl.box[3] - pl.box[1])} />
              </g>
            );
          })()}
        </svg>
        {drag && drag.dx !== undefined && (() => {
          const t = dragTarget(drag);
          const coords = [t.x, t.y].filter((v) => v !== undefined).join(", ");
          const whole = drag.mode === "attach" ? "whole control · " : "";
          return (
            <div className="drag-readout" role="status">
              {whole}({coords}) Δ({drag.dx}, {drag.dy}) · Esc cancels
            </div>
          );
        })()}
      </div>
      <details className="knobs">
        <summary>Preview</summary>
        {p.coachConfig && p.preview !== undefined && p.onPreview && (
          <CoachConfig config={p.coachConfig} preview={p.preview}
                       onChange={p.onPreview} onImportLive={p.onImportLive} />
        )}
        <label>tank level
          <input type="range" min={0} max={1} step={0.05}
                 value={p.knobs.tank_level ?? 0}
                 onChange={(e) => p.onKnobs({ ...p.knobs,
                   tank_level: Number(e.target.value) })} />
        </label>
        <details className="escape-hatch">
          <summary>raw slots</summary>
          <label>slots
            <input value={p.knobs.slots ?? ""} placeholder="5:8"
                   onChange={(e) => p.onKnobs({ ...p.knobs, slots: e.target.value })} />
          </label>
          <p className="hint">Overrides the coach config above for anything the
             picker does not model.</p>
        </details>
      </details>
    </>
  );
}
