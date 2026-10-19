// studio/web/src/api.ts
// The only module that knows API URLs. Everything else imports these functions and
// types. Errors arrive structured ({detail: {code, message, ...}}) and are re-thrown
// as ApiError with the code intact -- the UI renders fields, never parsed text.

export type OpSpec = { id: string; type: string; params: Record<string, unknown>;
                       enabled: boolean; note: string };
export type Project = { schema: number; base_sha256: string;
                        build_name: string | null; ops: OpSpec[];
                        preview?: Preview };
export type ProjectDoc = { name: string; mtime: number; project: Project };
export type CompileError = { op_id: string | null; code: string; message: string;
                             offers: string[] };
export type CompileResult = { ok: boolean; errors: CompileError[]; manifest: string[];
                              unverified: string[]; size: number | null; etag: string };
export type Placement = { obj_index: number; wtype: number; kind: string;
                          box: [number, number, number, number]; label: string | null;
                          font: number | null; editable: string[];
                          // The record's own raw stored (x, y) -- the coordinate space
                          // move_element writes into -- distinct from `box`, which is the
                          // layout-RESOLVED absolute placement (geom + register +
                          // container offset).
                          // A drag-to-move client must anchor its delta on `geom`, never
                          // on `box`, or a nested record lands at resolved+container
                          // offset instead of where the drag put it.
                          // EVERY key is independently null: record geometry is
                          // presence-masked and the combinations are ragged (a box with
                          // w/h but no x/y, a box with x but no y). `geom` itself is
                          // null only when the record carries none of the four. A DRAG
                          // must check x and y are non-null; a RESIZE uses whichever of
                          // w/h exist and marks the rest `optional` on the op.
                          geom: { x: number | null; y: number | null;
                                  w: number | null; h: number | null } | null };
export type Binding = { group: number; gesture: string; channels: number[] };
export type PlacementsDoc = { etag: string; page: string; root: number;
                              canvas: [number, number]; placements: Placement[];
                              objects: Record<string, {
                                binding: Binding | null;
                                // The page root's wtype-5 attach record for this
                                // object: the raw coordinates move_attach writes
                                // (each key independently optional), or null when the
                                // root carries none. Optional so older fixtures
                                // without it stay valid.
                                attach?: { x?: number; y?: number } | null;
                              }> };
export type Pin = { pin: number; var: number | null; bridged: boolean;
                    colour_index: number | null; commanded: boolean;
                    dimmable: boolean; warnings: string[] };
export type Channel = { channel: number; commanded: boolean; dimmable: boolean;
                        watched: boolean };
export type Colour = { rgb: [number, number, number]; static: number[]; resting: number[] };
export type Font = { slot: number; cell_height: number; glyph_count: number };
export type Page = { name: string; root: number };
export type CoachOption = { name: string; label: string; slot: number; value: number;
                            page: number };
export type SlotChoice = { value: number; label: string };
// A control derived from the file and fronted by a runtime gate slot -- 13.6's is the
// "Batteries" 1/2 selector. `slot` is written into preview.extra_slots.
// `page` is where the control actually is, or null when no page Studio can draw places
// it -- 13.6 has one of each. Null is NOT "the panel never draws it": object 247 is
// attached by none of the graph mechanisms either and is still on screen (Finding 15).
export type SlotGroup = { gate_slot: number; slot: number; object: number;
                          label: string; page: string | null; choices: SlotChoice[] };
// A setting the panel computes something else FROM. Carries the linkage and a name for
// what changes, never the arithmetic -- that lives server-side (reference.derived_slots)
// and is applied when the preview's slots are built, so the client cannot drift from it.
export type Derives = { source_slot: number; what: string; detail: string };
export type CoachConfigData = { floorplans: { value: number; code: string }[];
                                options: CoachOption[];
                                defaults: Record<string, Record<string, boolean>>;
                                slot_groups?: SlotGroup[];
                                derives?: Derives[] };
// Preview is persisted in the project but is NOT build input -- see CoachConfig.tsx.
export type Preview = { floorplan?: string; options?: string[];
                        extra_slots?: Record<string, number> };
export type OpSchema = { params: { properties: Record<string, any>; required: string[] };
                         doc: string; hardware_verified: boolean };
export type ExportResult = { filename: string; path: string; size: number; sha256: string;
                             etag: string; manifest: string[]; unverified: string[];
                             download: string };
// `preview` is a cache-buster only: the SERVER derives the real slots from the saved
// project preview, so the <img> src must change when the preview does.
export type Knobs = { root?: number; tank_level?: number; slots?: string; switches?: string;
                      preview?: string;
                      // Not a preview knob: an opaque cache-buster (the compile etag) so
                      // renderUrl's string changes whenever the compiled bytes do. Without
                      // it a same-string <img src> is a no-op for React's DOM diff, so the
                      // browser never re-requests the PNG and the overlay drifts from the
                      // pixels after an edit.
                      etag?: string };
export type Health = { status: string; stock_build_name: string; base_sha256: string };

export class ApiError extends Error {
  constructor(public status: number, public code: string, public detail: any) {
    super(typeof detail?.message === "string" ? detail.message : `HTTP ${status}`);
    this.name = "ApiError";
  }
}

const p = (name: string) => `/api/projects/${encodeURIComponent(name)}`;

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const res = await fetch(url, { headers: { "Content-Type": "application/json" },
                                 ...(init ?? {}) });
  if (!res.ok) {
    let detail: any = null;
    try { detail = (await res.json())?.detail ?? null; } catch { /* not JSON */ }
    throw new ApiError(res.status, detail?.code ?? "UNKNOWN", detail ?? {});
  }
  // A success with no body (204, or any empty-bodied response) has nothing for
  // res.json() to parse -- it would reject with a raw SyntaxError instead of the
  // ApiError this module promises. Short-circuit before parsing.
  if (res.status === 204) return undefined as T;
  const text = await res.text();
  if (text === "") return undefined as T;
  return JSON.parse(text) as T;
}

function query(k: Knobs): string {
  const q = new URLSearchParams();
  if (k.root !== undefined) q.set("root", String(k.root));
  if (k.tank_level !== undefined) q.set("tank_level", String(k.tank_level));
  if (k.slots) q.set("slots", k.slots);
  if (k.switches) q.set("switches", k.switches);
  if (k.etag) q.set("etag", k.etag);
  // Same role as `etag`: an ignored-by-the-server cache-buster, so the <img> src
  // changes when the saved preview does and React actually refetches the pixels.
  if (k.preview) q.set("preview", k.preview);
  const s = q.toString();
  return s ? `?${s}` : "";
}

export const getHealth = () => request<Health>("/api/health");
export const listProjects = () =>
  request<{ name: string; mtime: number }[]>("/api/projects");
export const createProject = (name: string) =>
  request<ProjectDoc>("/api/projects", { method: "POST", body: JSON.stringify({ name }) });
export const getProject = (name: string) => request<ProjectDoc>(p(name));
export const saveProject = (name: string, project: Project, expectedMtime: number | null) =>
  request<{ name: string; mtime: number }>(p(name), {
    method: "PUT", body: JSON.stringify({ project, expected_mtime: expectedMtime }) });
export const compile = (name: string) =>
  request<CompileResult>(`${p(name)}/compile`, { method: "POST" });
export const renderUrl = (name: string, page: string, k: Knobs) =>
  `${p(name)}/render/${encodeURIComponent(page)}${query(k)}`;
export const getPlacements = (name: string, page: string, k: Knobs) =>
  request<PlacementsDoc>(`${p(name)}/placements/${encodeURIComponent(page)}${query(k)}`);
export const getSchemas = () => request<Record<string, OpSchema>>("/api/schemas");
export const getPins = () => request<Pin[]>("/api/reference/pins");
export const getChannels = () => request<Channel[]>("/api/reference/channels");
export const getColours = () => request<Colour[]>("/api/reference/colors");
export const getFonts = () => request<Font[]>("/api/reference/fonts");
export const fontSpecimenUrl = (slot: number) => `/api/reference/fonts/${slot}/specimen`;
export const getPages = () => request<Page[]>("/api/reference/pages");
export const getCoachConfig = () => request<CoachConfigData>("/api/reference/config");

// Importing the coach's live config from a candump capture. Optional and opt-in: it
// takes TEXT, so the capture can come from any bridge/adapter/forwarded log, and the
// app never needs one. A capture with no status frames returns frames: 0 and an empty
// diff -- a message, never an error.
export type PreviewDiff = { field: string; from: unknown; to: unknown };
export type LiveImport = { preview: Preview; diff: PreviewDiff[]; frames: number;
                           source: string | null; message: string };
export const importLive = (name: string, capture: string) =>
  request<LiveImport>(`/api/projects/${encodeURIComponent(name)}/preview/import`,
                      { method: "POST", headers: { "Content-Type": "application/json" },
                        body: JSON.stringify({ capture }) });
export const exportBuild = (name: string) =>
  request<ExportResult>(`${p(name)}/export`, { method: "POST" });
