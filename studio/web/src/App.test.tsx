// studio/web/src/App.test.tsx
// App-level coverage for the two review-critical wiring points: onMove must anchor its
// delta on the record's raw geom (not the layout-resolved box) and pass an expect guard,
// and a record with no geom must be skipped rather than misplaced.
import { describe, it, expect, vi, afterEach } from "vitest";
import { render, fireEvent, waitFor, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import React from "react";
import App from "./App";
import * as api from "./api";

const wrapper = ({ children }: { children: React.ReactNode }) => {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return <QueryClientProvider client={qc}>{children}</QueryClientProvider>;
};

const projectDoc = { name: "default", mtime: 1,
                     project: { schema: 1, base_sha256: "a", build_name: null, ops: [] } };
const compileResult = { ok: true, errors: [], manifest: [], unverified: [],
                        size: 10, etag: "etag1" };
const pages = [{ name: "home", root: 4 }];

function placementsWithGeom(geom: { x: number; y: number } | null, label = "FRESH") {
  return {
    etag: "etag1", page: "home", root: 4, canvas: [800, 480],
    placements: [
      { obj_index: 10, wtype: 11, kind: "label", box: [120, 130, 200, 160],
        label, font: 13, editable: ["rename_label", "move_element"], geom },
    ],
    objects: { "10": { binding: null } },
  };
}

function stubEverythingExceptSave() {
  vi.spyOn(api, "getHealth").mockResolvedValue(
    { status: "ok", stock_build_name: "x", base_sha256: "y" } as any);
  vi.spyOn(api, "getProject").mockResolvedValue(projectDoc as any);
  vi.spyOn(api, "compile").mockResolvedValue(compileResult as any);
  vi.spyOn(api, "getPages").mockResolvedValue(pages as any);
  return vi.spyOn(api, "saveProject").mockResolvedValue({ name: "default", mtime: 2 } as any);
}

async function dragTheOnlyRect(container: HTMLElement) {
  await waitFor(() => expect(container.querySelectorAll("svg rect")).toHaveLength(1));
  const svg = container.querySelector("svg")!;
  // jsdom has no layout; Canvas reads coordinates via getBoundingClientRect (see
  // Canvas.test.tsx for the same stub).
  vi.spyOn(svg, "getBoundingClientRect").mockReturnValue(
    { left: 0, top: 0, width: 800, height: 480 } as DOMRect);
  const rect = container.querySelector("svg rect")!;
  fireEvent.mouseDown(rect, { clientX: 130, clientY: 140 });
  fireEvent.mouseUp(svg, { clientX: 140, clientY: 150 });   // dx=10, dy=10
}

afterEach(() => vi.restoreAllMocks());

describe("App drag-to-move", () => {
  it("emits move_element anchored on the record's RAW geom, with an expect guard", async () => {
    vi.spyOn(api, "getPlacements").mockResolvedValue(placementsWithGeom({ x: 30, y: 7 }) as any);
    const save = stubEverythingExceptSave();

    const { container } = render(<App />, { wrapper });
    await dragTheOnlyRect(container);

    await waitFor(() => expect(save).toHaveBeenCalled());
    const [, project] = save.mock.calls[0];
    expect(project.ops).toHaveLength(1);
    expect(project.ops[0]).toMatchObject({
      type: "move_element",
      params: {
        target: 10,
        selector: { wtype: 11, nth: 0 },
        // geom.x/y (30, 7) + the 10px drag -- NOT box[0]/box[1] (120, 130), which is
        // the layout-resolved space move_element does not write into.
        set: { x: 40, y: 17 },
        expect: { x: 30, y: 7 },
      },
    });
  });

  it("skips the op entirely when the dragged record has no raw geom", async () => {
    vi.spyOn(api, "getPlacements").mockResolvedValue(placementsWithGeom(null) as any);
    const save = stubEverythingExceptSave();

    const { container } = render(<App />, { wrapper });
    await dragTheOnlyRect(container);

    // Give the drag's mouseup handler a turn, then confirm no save ever fires -- there
    // is no server request to await for a no-op, so this polls the deliberate absence.
    await new Promise((r) => setTimeout(r, 50));
    expect(save).not.toHaveBeenCalled();
  });
});

describe("App — box drag moves the whole control", () => {
  function boxPlacements() {
    return {
      etag: "etag1", page: "home", root: 4, canvas: [800, 480],
      placements: [
        { obj_index: 10, wtype: 33, kind: "box", box: [100, 100, 300, 200],
          label: null, font: null, editable: ["recolor"],
          geom: { x: null, y: null, w: 200, h: 100 } },
      ],
      objects: { "10": { binding: null, attach: { x: 5, y: 7 } } },
    };
  }

  it("emits move_attach built from the ATTACH geometry, not the box", async () => {
    vi.spyOn(api, "getPlacements").mockResolvedValue(boxPlacements() as any);
    const save = stubEverythingExceptSave();

    const { container } = render(<App />, { wrapper });
    await dragTheOnlyRect(container);

    await waitFor(() => expect(save).toHaveBeenCalled());
    const [, project] = save.mock.calls[0];
    expect(project.ops).toHaveLength(1);
    expect(project.ops[0]).toMatchObject({
      type: "move_attach",
      // attach (5, 7) + the 10px drag -- the raw coordinates move_attach writes.
      params: { page: "home", child: 10, x: 15, y: 17 },
    });
  });

  it("skips the drag when the object has no attach record on this page", async () => {
    const doc = boxPlacements();
    (doc.objects as any)["10"].attach = null;
    vi.spyOn(api, "getPlacements").mockResolvedValue(doc as any);
    const save = stubEverythingExceptSave();

    const { container } = render(<App />, { wrapper });
    await dragTheOnlyRect(container);

    await new Promise((r) => setTimeout(r, 50));
    expect(save).not.toHaveBeenCalled();
  });
});

describe("App — placements are cached per page + etag", () => {
  it("returning to a visited page serves the cached placements, no refetch", async () => {
    const twoPages = [{ name: "home", root: 4 }, { name: "lights", root: 9 }];
    stubEverythingExceptSave();
    vi.spyOn(api, "getPages").mockResolvedValue(twoPages as any);
    const placements = vi.spyOn(api, "getPlacements")
      .mockImplementation(async (_n, page) => (
        { ...placementsWithGeom({ x: 1, y: 2 }), page } as any));

    render(<App />, { wrapper });
    await waitFor(() => expect(placements).toHaveBeenCalledTimes(1));
    fireEvent.click(screen.getByRole("button", { name: "lights" }));
    await waitFor(() => expect(placements).toHaveBeenCalledTimes(2));
    fireEvent.click(screen.getByRole("button", { name: "home" }));
    // The key (page + knobs + preview + compile etag) is unchanged since the first
    // fetch, so this must come from the query cache. The absence of a third request
    // has nothing to await; give a refetch every chance to fire, then check.
    await new Promise((r) => setTimeout(r, 50));
    expect(placements).toHaveBeenCalledTimes(2);
  });
});

// FINDING 1: a stateful mock that behaves like the real server closely enough to
// exercise "the SECOND edit of any field" -- getProject/saveProject/compile track a
// mutable `mtime`/`ops`, so a save really does change what the NEXT getPlacements call
// reports, the way a real save -> invalidate -> refetch -> recompile cycle does.
function stubStatefulProject(placementsForMtime: (mtime: number) => any) {
  let mtime = 1;
  let ops: any[] = [];
  vi.spyOn(api, "getHealth").mockResolvedValue(
    { status: "ok", stock_build_name: "x", base_sha256: "y" } as any);
  vi.spyOn(api, "getPages").mockResolvedValue(pages as any);
  vi.spyOn(api, "getProject").mockImplementation(async () => (
    { name: "default", mtime,
      project: { schema: 1, base_sha256: "a", build_name: null, ops } }));
  const save = vi.spyOn(api, "saveProject").mockImplementation(async (_n, project) => {
    ops = project.ops;
    mtime += 1;
    return { name: "default", mtime };
  });
  vi.spyOn(api, "compile").mockImplementation(async () => (
    { ok: true, errors: [], manifest: [], unverified: [], size: 10,
      etag: `etag${mtime}` }));
  const placements = vi.spyOn(api, "getPlacements").mockImplementation(async () => (
    placementsForMtime(mtime)));
  return { save, placements, mtimeRef: () => mtime };
}

describe("App — Finding 1: pre-image staleness under upsert", () => {
  it("two successive drags collapse into one op whose expect still names the "
     + "ORIGINAL geometry, not the post-first-drag one", async () => {
    // mtime 1 (stock): geom (30, 7). mtime 2 (after the first save "compiled"):
    // geom (40, 17) -- as if the panel now reports the moved position, the way
    // Finding 1 describes ("placements now report S+d1").
    const { save } = stubStatefulProject((mtime) =>
      placementsWithGeom(mtime === 1 ? { x: 30, y: 7 } : { x: 40, y: 17 }));

    const { container } = render(<App />, { wrapper });
    await dragTheOnlyRect(container);
    await waitFor(() => expect(save).toHaveBeenCalledTimes(1));

    // Wait for the post-save refetch chain (doc -> compile -> placements) to land the
    // NEW geometry before dragging again -- otherwise this wouldn't exercise staleness
    // at all, it would just re-drag from the same numbers.
    await waitFor(() => expect(api.getPlacements).toHaveBeenCalledTimes(2));

    await dragTheOnlyRect(container);
    await waitFor(() => expect(save).toHaveBeenCalledTimes(2));

    const [, secondProject] = save.mock.calls[1];
    expect(secondProject.ops).toHaveLength(1);          // collapsed, not appended
    expect(secondProject.ops[0].params.set).toEqual({ x: 50, y: 27 });   // 40+10, 17+10
    // The pre-image: STOCK's geometry (30, 7), never the intermediate (40, 17).
    expect(secondProject.ops[0].params.expect).toEqual({ x: 30, y: 7 });
  });

  it("two successive renames collapse into one op whose old still names the "
     + "stock text, not the post-first-rename one", async () => {
    const { save } = stubStatefulProject((mtime) =>
      placementsWithGeom({ x: 30, y: 7 }, mtime === 1 ? "FRESH" : "Fresh Water"));

    const { container } = render(<App />, { wrapper });
    await selectTheOnlyRect(container);

    await waitFor(() => expect(screen.getByDisplayValue("FRESH")).toBeInTheDocument());
    await renameTo("Fresh Water");
    await waitFor(() => expect(save).toHaveBeenCalledTimes(1));
    expect(save.mock.calls[0][1].ops[0].params).toMatchObject(
      { old: "FRESH", new: "Fresh Water" });

    // Wait for the refetch to land the new label, then re-select the object -- this is
    // the exact scenario Finding 1 names: "reselect after a rename and `old` becomes
    // the renamed text".
    await waitFor(() => expect(api.getPlacements).toHaveBeenCalledTimes(2));
    await selectTheOnlyRect(container);
    await waitFor(() => expect(screen.getByDisplayValue("Fresh Water")).toBeInTheDocument());

    await renameTo("Fresh Water Extra");
    await waitFor(() => expect(save).toHaveBeenCalledTimes(2));

    const secondOps = save.mock.calls[1][1].ops;
    expect(secondOps).toHaveLength(1);                  // collapsed, not appended
    expect(secondOps[0].params.new).toBe("Fresh Water Extra");
    // The pre-image: STOCK's text ("FRESH"), never the intermediate ("Fresh Water").
    expect(secondOps[0].params.old).toBe("FRESH");
  });
});

async function selectTheOnlyRect(container: HTMLElement) {
  await waitFor(() => expect(container.querySelectorAll("svg rect")).toHaveLength(1));
  const svg = container.querySelector("svg")!;
  vi.spyOn(svg, "getBoundingClientRect").mockReturnValue(
    { left: 0, top: 0, width: 800, height: 480 } as DOMRect);
  fireEvent.click(svg, { clientX: 130, clientY: 140 });
}

async function renameTo(text: string) {
  const input = screen.getByLabelText(/^label$/i);
  await userEvent.clear(input);
  await userEvent.type(input, text);
  await userEvent.click(screen.getByRole("button", { name: /apply label/i }));
}

describe("App — Finding 2: onOffer wiring", () => {
  it("clicking an offer button applies its flag and re-saves", async () => {
    const ops = [{ id: "a", type: "rename_label",
                  params: { object: 10, old: "FRESH", new: "A Much Longer Caption" },
                  enabled: true, note: "" }];
    vi.spyOn(api, "getHealth").mockResolvedValue(
      { status: "ok", stock_build_name: "x", base_sha256: "y" } as any);
    vi.spyOn(api, "getPages").mockResolvedValue(pages as any);
    vi.spyOn(api, "getProject").mockResolvedValue(
      { name: "default", mtime: 1,
        project: { schema: 1, base_sha256: "a", build_name: null, ops } } as any);
    vi.spyOn(api, "compile").mockResolvedValue({
      ok: false, size: null, etag: "e1", manifest: [], unverified: [],
      errors: [{ op_id: "a", code: "LABEL_TOO_LONG", message: "does not fit",
                offers: ["grow", "shorten"] }],
    } as any);
    const save = vi.spyOn(api, "saveProject")
      .mockResolvedValue({ name: "default", mtime: 2 } as any);

    const { findByRole } = render(<App />, { wrapper });
    const grow = await findByRole("button", { name: /^grow$/i });
    await userEvent.click(grow);

    await waitFor(() => expect(save).toHaveBeenCalled());
    const [, project] = save.mock.calls[0];
    expect(project.ops[0].params.allow_grow).toBe(true);
  });
});

describe("App — Finding 3: a missing project is usable, not a blank shell", () => {
  it("a 404 doc load surfaces a toast and a create-project affordance", async () => {
    vi.spyOn(api, "getHealth").mockResolvedValue(
      { status: "ok", stock_build_name: "x", base_sha256: "y" } as any);
    vi.spyOn(api, "getPages").mockResolvedValue(pages as any);
    vi.spyOn(api, "getProject").mockRejectedValue(
      new api.ApiError(404, "NOT_FOUND", { message: "no project 'default'" }));
    vi.spyOn(api, "listProjects").mockResolvedValue([]);

    const { findByRole, findByText } = render(<App />, { wrapper });
    await findByText(/NOT_FOUND|no project/i);
    await findByRole("button", { name: /create project/i });
  });

  it("the create affordance calls createProject with the project name", async () => {
    vi.spyOn(api, "getHealth").mockResolvedValue(
      { status: "ok", stock_build_name: "x", base_sha256: "y" } as any);
    vi.spyOn(api, "getPages").mockResolvedValue(pages as any);
    vi.spyOn(api, "getProject").mockRejectedValue(
      new api.ApiError(404, "NOT_FOUND", { message: "no project 'default'" }));
    vi.spyOn(api, "listProjects").mockResolvedValue([]);
    const create = vi.spyOn(api, "createProject").mockResolvedValue(
      { name: "default", mtime: 1,
        project: { schema: 1, base_sha256: "a", build_name: null, ops: [] } } as any);

    const { findByRole } = render(<App />, { wrapper });
    const button = await findByRole("button", { name: /create project/i });
    await userEvent.click(button);

    await waitFor(() => expect(create).toHaveBeenCalledWith("default"));
  });
});

describe("App — Finding 4: a save in flight suppresses a second mutation", () => {
  it("a second drag while the first save is still pending does not call save again",
     async () => {
    vi.spyOn(api, "getPlacements").mockResolvedValue(placementsWithGeom({ x: 30, y: 7 }) as any);
    vi.spyOn(api, "getHealth").mockResolvedValue(
      { status: "ok", stock_build_name: "x", base_sha256: "y" } as any);
    vi.spyOn(api, "getProject").mockResolvedValue(projectDoc as any);
    vi.spyOn(api, "compile").mockResolvedValue(compileResult as any);
    vi.spyOn(api, "getPages").mockResolvedValue(pages as any);
    let resolveSave: (v: any) => void = () => {};
    const save = vi.spyOn(api, "saveProject").mockImplementation(
      () => new Promise((res) => { resolveSave = res; }));

    const { container } = render(<App />, { wrapper });
    await dragTheOnlyRect(container);
    await waitFor(() => expect(save).toHaveBeenCalledTimes(1));

    // The first save is still pending (never resolved) -- proj.isSaving is true.
    await dragTheOnlyRect(container);
    await new Promise((r) => setTimeout(r, 50));
    expect(save).toHaveBeenCalledTimes(1);      // the second drag was suppressed

    resolveSave({ name: "default", mtime: 2 });
  });
});

// ---------------------------------------------------------------------------
// Build name — the string the coach's boot menu lists. A project FIELD, not an op,
// so it deliberately produces no change-list entry; the topbar field is its display.

describe("App — build name", () => {
  it("shows the project's build name and saves an edit as a field, not an op",
     async () => {
    const save = stubEverythingExceptSave();
    vi.spyOn(api, "getProject").mockResolvedValue({
      name: "default", mtime: 1,
      project: { schema: 1, base_sha256: "a",
                 build_name: "Jayco B-Van V13 (Studio)", ops: [] },
    } as any);
    vi.spyOn(api, "getPlacements").mockResolvedValue(placementsWithGeom(null) as any);
    render(<App />, { wrapper });

    // the field mounts before the project query settles, so wait for the loaded value
    await waitFor(() => expect(screen.getByLabelText(/build name/i))
      .toHaveValue("Jayco B-Van V13 (Studio)"));
    const field = screen.getByLabelText(/build name/i);

    await userEvent.clear(field);
    await userEvent.type(field, "Jayco B-Van V13 (Custom)");
    fireEvent.blur(field);

    await waitFor(() => expect(save).toHaveBeenCalled());
    const [, project] = save.mock.calls[save.mock.calls.length - 1];
    expect((project as any).build_name).toBe("Jayco B-Van V13 (Custom)");
    // a field, not an op: nothing was appended to the change list
    expect((project as any).ops).toEqual([]);
  });

  it("caps the field at the header's usable capacity", async () => {
    stubEverythingExceptSave();
    vi.spyOn(api, "getPlacements").mockResolvedValue(placementsWithGeom(null) as any);
    render(<App />, { wrapper });
    const field = await screen.findByLabelText(/build name/i);
    expect(field).toHaveAttribute("maxLength", "127");
  });
});

// ---------------------------------------------------------------------------
// The "+ Add" catalogue: the curated Button card, plus a generated form for every op
// with no hand-written control — so a newly-registered engine op is reachable.

const schemasStub = {
  add_button: { doc: "Add a button", hardware_verified: true,
                params: { properties: { channel: { type: "integer" } },
                          required: ["channel"] } },
  move_attach: { doc: "Move a control's attach point", hardware_verified: true,
                 params: { properties: {
                   page: { type: ["string", "integer"] },
                   child: { type: ["integer", "object"] } },
                   required: ["page", "child"] } },
  install_glyph: { doc: "Install an authored glyph", hardware_verified: true,
                   params: { properties: {
                     slot: { type: "integer" },
                     rows: { type: "array", items: { type: "string" } } },
                     required: ["slot", "rows"] } },
  // Stands in for "an op whose params a text box cannot honestly collect". That used
  // to be install_glyph, which is curated now (the artwork importer collects its
  // pixels), so the rule needs an uncurated op to demonstrate it.
  paint_region: { doc: "Paint a region from pixel rows", hardware_verified: true,
                  params: { properties: {
                    object: { type: "integer" },
                    rows: { type: "array", items: { type: "string" } } },
                    required: ["object", "rows"] } },
};

describe("App — add catalogue", () => {
  it("offers the curated button card and the uncurated ops, not the curated ones",
     async () => {
    stubEverythingExceptSave();
    vi.spyOn(api, "getPlacements").mockResolvedValue(placementsWithGeom(null) as any);
    vi.spyOn(api, "getSchemas").mockResolvedValue(schemasStub as any);
    render(<App />, { wrapper });

    await userEvent.click(await screen.findByRole("button", { name: /\+ add/i }));
    expect(screen.getByRole("button", { name: /^button$/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /move_attach/i })).toBeInTheDocument();
    // add_button has a curated card, so it must not also appear as a raw op
    expect(screen.queryByRole("button", { name: /^add_button$/ })).toBeNull();
  });

  it("disables an op a generated form cannot honestly collect, and says why",
     async () => {
    stubEverythingExceptSave();
    vi.spyOn(api, "getPlacements").mockResolvedValue(placementsWithGeom(null) as any);
    vi.spyOn(api, "getSchemas").mockResolvedValue(schemasStub as any);
    render(<App />, { wrapper });

    await userEvent.click(await screen.findByRole("button", { name: /\+ add/i }));
    expect(screen.getByRole("button", { name: /paint_region/i })).toBeDisabled();
    expect(screen.getByText(/needs an importer for rows/i)).toBeInTheDocument();
  });

  it("adds the op the generated form produces to the change list", async () => {
    const save = stubEverythingExceptSave();
    vi.spyOn(api, "getPlacements").mockResolvedValue(placementsWithGeom(null) as any);
    vi.spyOn(api, "getSchemas").mockResolvedValue(schemasStub as any);
    render(<App />, { wrapper });

    await userEvent.click(await screen.findByRole("button", { name: /\+ add/i }));
    await userEvent.click(screen.getByRole("button", { name: /move_attach/i }));
    await userEvent.type(screen.getByLabelText(/^page/i), "home");
    await userEvent.type(screen.getByLabelText(/^child/i), "42");
    await userEvent.click(screen.getByRole("button", { name: /add this change/i }));

    await waitFor(() => expect(save).toHaveBeenCalled());
    const [, project] = save.mock.calls[save.mock.calls.length - 1];
    expect((project as any).ops).toHaveLength(1);
    expect((project as any).ops[0]).toMatchObject({
      type: "move_attach", params: { page: "home", child: 42 },
    });
  });
});
