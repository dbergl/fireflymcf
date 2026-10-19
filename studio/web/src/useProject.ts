// studio/web/src/useProject.ts
// All server state for one project. The project FILE is the document: every edit is
// a save followed by a recompile, so what the canvas shows is always what the
// engine would build. The client caches nothing the server owns.
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import * as api from "./api";
import type { CompileResult, OpSpec, ProjectDoc } from "./api";

function toApiError(e: unknown): api.ApiError {
  return e instanceof api.ApiError ? e : new api.ApiError(0, "UNKNOWN", { message: String(e) });
}

export type ProjectState = {
  name: string;
  doc: ProjectDoc | undefined;
  ops: OpSpec[];
  compile: CompileResult | undefined;
  isSaving: boolean;
  error: api.ApiError | null;
  mutate: (next: OpSpec[]) => void;
  // The build name is a project FIELD, not an op (recipe.compile_project applies it
  // once at the end), so it has its own setter rather than riding the op list.
  setBuildName: (name: string) => void;
  // Preview is the coach-config the CANVAS renders under (floorplan + options). It is
  // persisted in the project for convenience only and is NOT build input -- the compile
  // etag excludes it by construction, so setting it never invalidates a build.
  preview: api.Preview;
  setPreview: (preview: api.Preview) => void;
  clearError: () => void;
};

export function useProject(name: string): ProjectState {
  const qc = useQueryClient();
  const [error, setError] = useState<api.ApiError | null>(null);

  const doc = useQuery({ queryKey: ["project", name],
                         queryFn: () => api.getProject(name), enabled: !!name });
  const compiled = useQuery({
    queryKey: ["compile", name, doc.data?.mtime],
    queryFn: () => api.compile(name),
    enabled: !!doc.data,
  });

  const save = useMutation({
    mutationFn: (project: api.Project) =>
      api.saveProject(name, project, doc.data?.mtime ?? null),
    onMutate: () => setError(null),
    onError: (e) => setError(toApiError(e)),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["project", name] }),
  });

  // A failed compile is a failure too, and `error` is documented as "last
  // save/compile failure" -- without this a bad compile leaves the header on "…"
  // forever with no toast, indistinguishable from still loading.
  useEffect(() => {
    if (compiled.error) setError(toApiError(compiled.error));
  }, [compiled.error]);

  // FINDING 3 fix: a doc load failure (most commonly a 404 for a project that has
  // never been created) used to vanish -- `doc.isError` reached nothing, so a
  // first-time user following the README got a blank shell with no error and no way
  // forward. Route it through the same `error` surface save/compile already use, so
  // App's toast (and, for NOT_FOUND specifically, the create-project affordance) has
  // something to react to.
  useEffect(() => {
    if (doc.error) setError(toApiError(doc.error));
  }, [doc.error]);

  const project = doc.data?.project;
  return {
    name,
    doc: doc.data,
    ops: project?.ops ?? [],
    compile: compiled.data,
    isSaving: save.isPending,
    error,
    mutate: (next) => project && save.mutate({ ...project, ops: next }),
    setBuildName: (bn) => project && save.mutate({ ...project, build_name: bn }),
    preview: project?.preview ?? {},
    setPreview: (pv) => project && save.mutate({ ...project, preview: pv }),
    // Callers that just fixed the underlying condition (e.g. the create-project
    // affordance, right after `api.createProject` succeeds) can drop a stale error
    // immediately rather than waiting on a query to settle.
    clearError: () => setError(null),
  };
}
