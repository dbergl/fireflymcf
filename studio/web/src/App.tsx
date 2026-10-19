import { useEffect, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import * as api from "./api";
import * as opsLib from "./ops";
import { useProject } from "./useProject";
import ChangeList from "./panes/ChangeList";
import Canvas from "./panes/Canvas";
import Inspector from "./panes/Inspector";
import AddButton from "./panes/AddButton";
import GlyphButton from "./panes/GlyphButton";
import ImportGlyph from "./panes/ImportGlyph";
import OpForm from "./panes/OpForm";
import { uncuratedOps } from "./opforms";
import { moveAttachDraft } from "./geomedit";
import ExportDialog from "./ExportDialog";
import { refreshSelection } from "./selection";
import type { Selection } from "./selection";

// FINDING 1 fix: which of a draft's own params are a PRE-IMAGE that must always
// describe STOCK, never the currently-compiled image. Only these two op types carry
// one -- everything else emits a value-only draft with nothing to preserve.
function preserveKeysFor(type: string): string[] {
  if (type === "rename_label") return ["old"];
  if (type === "move_element") return ["expect"];
  return [];
}

export default function App() {
  const params = new URLSearchParams(location.search);
  const name = params.get("project") ?? "default";
  const qc = useQueryClient();
  const health = useQuery({ queryKey: ["health"], queryFn: api.getHealth });
  const proj = useProject(name);
  const pages = useQuery({ queryKey: ["pages"], queryFn: api.getPages });
  const colours = useQuery({ queryKey: ["colours"], queryFn: api.getColours });
  const fonts = useQuery({ queryKey: ["fonts"], queryFn: api.getFonts });
  const pins = useQuery({ queryKey: ["pins"], queryFn: api.getPins });
  const channels = useQuery({ queryKey: ["channels"], queryFn: api.getChannels });
  const schemas = useQuery({ queryKey: ["schemas"], queryFn: api.getSchemas });
  const coach = useQuery({ queryKey: ["coach-config"], queryFn: api.getCoachConfig });
  const [page, setPage] = useState("home");
  const [knobs, setKnobs] = useState<api.Knobs>({});
  const [selection, setSelection] = useState<Selection | null>(null);
  // The add flow is a small catalogue, not a single card: "catalog" lists what can be
  // added, "button" is the curated add-a-button card, and any other string is an op
  // type opening a generated form (see opforms.ts).
  const [adding, setAdding] = useState<null | "catalog" | "button" | string>(null);
  const [exporting, setExporting] = useState(false);
  const [creating, setCreating] = useState(false);
  const [createError, setCreateError] = useState<api.ApiError | null>(null);
  const placements = useQuery({
    queryKey: ["placements", name, page, knobs, proj.preview, proj.compile?.etag],
    queryFn: () => api.getPlacements(name, page, knobs),
    enabled: !!proj.compile?.ok,
    // The key covers everything the response depends on (page, knobs, preview AND
    // the compile etag), so a cached entry can never be stale -- any edit changes the
    // etag and thus the key. Infinity makes switching back to a visited page instant
    // instead of refetching and greying the canvas out.
    staleTime: Infinity,
  });
  const errorsById = opsLib.errorsById(proj.compile?.errors ?? []);

  // Every applied op recompiles the image, so a selection made before the edit now
  // describes stale geometry -- and the inspector would build its next op's `expect`
  // from it. Re-point the selection whenever fresh placements arrive, so a second edit
  // to the same control just works instead of needing another click.
  const placementsDoc = placements.data;
  useEffect(() => {
    if (!placementsDoc) return;
    setSelection((current) => (current
      ? refreshSelection(placementsDoc, current) : current));
  }, [placementsDoc]);

  // FINDING 3: the project named by ?project= (default "default") may not exist yet --
  // the server 404s with NOT_FOUND, which now reaches `proj.error` (useProject wires
  // doc.isError into the same toast surface save/compile use). List existing projects
  // too, cheaply, so the affordance can offer them as an alternative to creating a new
  // one -- only fetched once there is actually a missing-project condition to show.
  const notFound = !proj.doc && proj.error?.code === "NOT_FOUND";
  const existing = useQuery({ queryKey: ["projects-list"], queryFn: api.listProjects,
                             enabled: notFound });

  // FINDING 4 fix: the in-flight-save guard used to cover only ChangeList's checkbox/
  // remove/note controls (via their `disabled` prop) -- ChangeList's drag-reorder,
  // Canvas drag, every Inspector action and AddButton's create all rebuilt `next` from
  // the pre-save `proj.ops`, so any of them firing while a save was still in flight
  // would silently overwrite whatever that save was about to persist. Gating every
  // mutation-producing call through this one function, here, covers all six without
  // threading a `disabled` prop through five components.
  const guardedMutate = (next: api.OpSpec[]) => {
    if (proj.isSaving) return;
    proj.mutate(next);
  };

  const handleCreateProject = async () => {
    setCreating(true);
    setCreateError(null);
    try {
      await api.createProject(name);
      proj.clearError();
      await qc.invalidateQueries({ queryKey: ["project", name] });
    } catch (e) {
      setCreateError(e instanceof api.ApiError
        ? e : new api.ApiError(0, "UNKNOWN", { message: String(e) }));
    } finally {
      setCreating(false);
    }
  };

  return (
    <div className="shell">
      <header className="topbar">
        <strong>Firefly Studio</strong>
        <span className="muted">{name}</span>
        <span className="muted">{health.data?.stock_build_name ?? "…"}</span>
        {/* The build name is what the coach's BOOT MENU lists -- distinct from the
            export filename (mcf-<project>.bin). It is a project field rather than an
            op, so by design it makes no change-list entry; this field is its display.
            maxLength is the header's real usable capacity (128-byte NUL-terminated
            field), so the common mistake fails here instead of at compile; a
            non-ASCII name still falls to the engine's BAD_BUILD_NAME refusal. */}
        <label className="buildname">
          <span className="muted">Build name</span>
          <input aria-label="build name" maxLength={127}
                 defaultValue={proj.doc?.project.build_name ?? ""}
                 key={proj.doc?.project.build_name ?? ""}
                 placeholder="(unset — the stock name is kept)"
                 disabled={proj.isSaving}
                 onBlur={(e) => {
                   const next = e.target.value;
                   if (next !== (proj.doc?.project.build_name ?? "")) {
                     proj.setBuildName(next);
                   }
                 }} />
        </label>
        <span className="muted">
          {proj.compile ? (proj.compile.ok ? "compiled ✓" : "compile failed") : "…"}
        </span>
      </header>
      {notFound ? (
        <div className="toast create-project" role="alert">
          <p>No project named "{name}" yet.</p>
          <button disabled={creating} onClick={handleCreateProject}>
            {creating ? "creating…" : `create project "${name}"`}
          </button>
          {!!existing.data?.length && (
            <p>
              Or open an existing one:{" "}
              {existing.data.map((p, i) => (
                <span key={p.name}>
                  {i > 0 && ", "}
                  <a href={`?project=${encodeURIComponent(p.name)}`}>{p.name}</a>
                </span>
              ))}
            </p>
          )}
          {createError && (
            <p className="warn"><strong>{createError.code}</strong> {createError.message}</p>
          )}
        </div>
      ) : (
        <main className="panes">
          <aside className="pane changes">
            <ChangeList
              ops={proj.ops} errorsById={errorsById}
              onReorder={(f, t) => guardedMutate(opsLib.reorderOp(proj.ops, f, t))}
              onToggle={(id) => guardedMutate(opsLib.toggleOp(proj.ops, id))}
              onRemove={(id) => guardedMutate(opsLib.removeOp(proj.ops, id))}
              onNote={(id, n) => guardedMutate(opsLib.setNote(proj.ops, id, n))}
              onOffer={(id, offer) => guardedMutate(opsLib.applyOffer(proj.ops, id, offer))}
              onAdd={() => setAdding("catalog")} onExport={() => setExporting(true)}
              disabled={proj.isSaving} />
          </aside>
          <section className="pane canvas">
            <Canvas name={name} page={page} pages={pages.data ?? []}
                    placements={placements.data}
                    // Clicks are ignored until the overlay matches the pixels; say so
                    // rather than swallowing them.
                    busy={placements.isFetching || proj.isSaving
                          || !placements.data}
                    // The <img> src is built from `knobs`, so it must carry the compile
                    // etag as a pure cache-buster -- otherwise a same-string src is a
                    // React DOM-diff no-op and the pixels never refresh after an edit
                    // (the overlay would update while the render stayed stale). Strip it
                    // back out before it reaches `knobs` state / the placements query, so
                    // that query key stays keyed on proj.compile?.etag exactly once.
                    knobs={{ ...knobs, etag: proj.compile?.etag,
                             preview: JSON.stringify(proj.preview) }}
                    coachConfig={coach.data}
                    preview={proj.preview}
                    onPreview={proj.setPreview}
                    onImportLive={(capture) => api.importLive(name, capture)}
                    onPageChange={(pg) => { setPage(pg); setSelection(null); }}
                    onKnobs={({ etag: _etag, ...rest }) => setKnobs(rest)}
                    selection={selection} onSelect={setSelection}
                    onMove={(pl, dx, dy) => {
                      // `pl.box` is the layout-RESOLVED absolute placement (geom +
                      // register + container offset); move_element writes the record's
                      // own RAW stored geometry (records.geom_offset). For any record
                      // nested inside a container those differ by the container's
                      // offset, so the delta must be applied to `pl.geom`, never to
                      // `pl.box` -- writing box-based coordinates would silently land
                      // the element at resolved+container_offset. A record with no x/y
                      // field (pl.geom === null) has nothing to move; skip it.
                      // Presence-masked geometry: a record can carry w/h and no x/y
                      // at all, or an x with no y. A translation needs both, so a drag
                      // on such a record is a no-op rather than `null + dy` arithmetic
                      // writing a nonsense coordinate. Resizing those records is the
                      // inspector's geometry rows, which edit each field on its own.
                      if (!pl.geom || pl.geom.x === null || pl.geom.y === null) return;
                      const sel = { wtype: pl.wtype, nth: 0 };
                      guardedMutate(opsLib.upsertOp(proj.ops, {
                        type: "move_element",
                        params: { target: pl.obj_index, selector: sel,
                                  set: { x: pl.geom.x + dx, y: pl.geom.y + dy },
                                  // The engine's own EXPECT_MISMATCH guard then refuses a
                                  // stale placement (the record moved server-side since
                                  // this box was fetched) loudly, instead of writing a
                                  // move that lands somewhere the user never saw.
                                  // FINDING 1: `expect` is a pre-image and must always
                                  // name STOCK -- upsertOp's `preserve: ["expect"]`
                                  // keeps whatever the EXISTING op already recorded
                                  // rather than this (possibly stale) recomputation,
                                  // whenever this drag collapses into a prior one.
                                  expect: { x: pl.geom.x, y: pl.geom.y } },
                      }, preserveKeysFor("move_element")));
                    }}
                    onMoveAttach={(pl, dx, dy) => {
                      // A box drag moves the WHOLE control: one move_attach on the
                      // page's attach record, built from the server-supplied attach
                      // geometry (never from `box`, which is layout-resolved). No
                      // attach record on this page -> nothing the op could write ->
                      // the drag is a no-op, mirroring the geom-less label case above.
                      const attach = placements.data?.objects[String(pl.obj_index)]?.attach;
                      const draft = moveAttachDraft(page, pl.obj_index, attach, dx, dy);
                      if (draft) guardedMutate(opsLib.upsertOp(proj.ops, draft));
                    }} />
          </section>
          <aside className="pane inspector">
            <Inspector
              selection={selection}
              page={page}
              binding={selection ? (placements.data?.objects[String(selection.obj_index)]
                                      ?.binding ?? null) : null}
              colours={colours.data ?? []} fonts={fonts.data ?? []}
              pins={pins.data ?? []} channels={channels.data ?? []}
              schemas={schemas.data ?? {}}
              // FINDING 1: rename_label's `old` is a pre-image (stock's own text) and
              // must survive a second rename unchanged -- preserveKeysFor keeps it from
              // the existing op on update; every other op type has nothing to preserve.
              onOp={(d) => guardedMutate(opsLib.upsertOp(
                proj.ops, opsLib.withResolvedPreImage(proj.ops, d),
                preserveKeysFor(d.type)))}
              // A batch: resizing a control is one gesture but three records, so the
              // three move_element ops must land in ONE save -- folding them through
              // applyOpDrafts keeps that atomic, where three separate onOp calls would
              // each rebuild from the same pre-save op list and lose two of the three.
              onOps={(drafts) => guardedMutate(opsLib.applyOpDrafts(proj.ops, drafts))}
              // v1 limitation, deliberate not overlooked: this always emits
              // remove_control, even for a control THIS session just added with
              // AddButton. The nicer move -- deleting the add_button op outright --
              // needs a map from an op id to the object index it created, and the
              // server doesn't expose that in structured form (the compile manifest
              // only names it in prose, which the client is not allowed to parse).
              // remove_control is correct either way, just more verbose than
              // necessary for a same-session add; revisit once the server returns
              // that mapping.
              onRemoveControl={() => selection && guardedMutate(opsLib.upsertOp(proj.ops, {
                type: "remove_control", params: { object: selection.obj_index } }))} />
          </aside>
        </main>
      )}
      {adding === "catalog" && (
        <div className="card">
          <h3>Add a change</h3>
          <button onClick={() => setAdding("button")}>Button</button>
          <p className="hint">
            a boxed button on HOME, wired to a pin
          </p>
          <button onClick={() => setAdding("glyph-button")}>Glyph button</button>
          <p className="hint">
            an icon button cloned from a donor, wired to a pin in the same step
          </p>
          <button onClick={() => setAdding("import-glyph")}>Import a glyph</button>
          <p className="hint">
            artwork (PNG) into an icon font — its alpha becomes the glyph
          </p>
          {/* Everything the engine can do that has no hand-written control. Derived by
              subtracting the curated list from /api/schemas, so an op registered in the
              engine tomorrow shows up here with no change to this file. */}
          {uncuratedOps(schemas.data ?? {}).map((entry) => (
            <div key={entry.type} className="catalog-entry">
              <button disabled={!!entry.unsupported}
                      onClick={() => setAdding(entry.type)}>
                {entry.type}
              </button>
              <p className="hint">
                {entry.unsupported ?? entry.doc}
                {entry.hardwareVerified ? "" : " · never flashed to a coach"}
              </p>
            </div>
          ))}
          <div className="card-actions">
            <button onClick={() => setAdding(null)}>cancel</button>
          </div>
        </div>
      )}
      {adding === "glyph-button" && (
        <GlyphButton pins={pins.data ?? []} colours={colours.data ?? []}
                     fonts={fonts.data ?? []} pages={pages.data ?? []}
                     onCancel={() => setAdding(null)}
                     onCreate={(drafts) => {
                       guardedMutate(opsLib.applyOpDrafts(proj.ops, drafts));
                       setAdding(null);
                     }} />
      )}
      {adding === "import-glyph" && (
        <ImportGlyph fonts={fonts.data ?? []}
                     onCancel={() => setAdding(null)}
                     onCreate={(draft) => {
                       guardedMutate(opsLib.upsertOp(proj.ops, draft));
                       setAdding(null);
                     }} />
      )}
      {adding === "button" && (
        <AddButton pins={pins.data ?? []} colours={colours.data ?? []}
                   fonts={fonts.data ?? []}
                   onCancel={() => setAdding(null)}
                   onCreate={(drafts) => {
                     guardedMutate(opsLib.applyOpDrafts(proj.ops, drafts));
                     setAdding(null);
                   }} />
      )}
      {adding && !["catalog", "button", "glyph-button", "import-glyph"].includes(adding)
        && schemas.data?.[adding] && (
        <OpForm type={adding} schema={schemas.data[adding]}
                onCancel={() => setAdding(null)}
                onCreate={(draft) => {
                  guardedMutate(opsLib.upsertOp(proj.ops, draft));
                  setAdding(null);
                }} />
      )}
      {exporting && (
        <ExportDialog name={name} compile={proj.compile}
                      buildName={proj.doc?.project.build_name ?? null}
                      onClose={() => setExporting(false)} />
      )}
      {!notFound && proj.error && (
        <div className="toast" role="alert">
          <strong>{proj.error.code}</strong> {proj.error.message}
        </div>
      )}
    </div>
  );
}
