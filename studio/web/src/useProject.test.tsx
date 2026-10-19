import { describe, it, expect, vi, afterEach } from "vitest";
import { renderHook, waitFor, act } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import React from "react";
import { useProject } from "./useProject";
import * as api from "./api";

const wrapper = ({ children }: { children: React.ReactNode }) => {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return <QueryClientProvider client={qc}>{children}</QueryClientProvider>;
};

const doc = { name: "p", mtime: 1,
              project: { schema: 1, base_sha256: "a", build_name: null, ops: [] } };

afterEach(() => vi.restoreAllMocks());

describe("useProject", () => {
  it("loads the project and compiles it", async () => {
    vi.spyOn(api, "getProject").mockResolvedValue(doc as any);
    vi.spyOn(api, "compile").mockResolvedValue({ ok: true, errors: [], manifest: [],
      unverified: [], size: 10, etag: "e" } as any);
    const { result } = renderHook(() => useProject("p"), { wrapper });
    await waitFor(() => expect(result.current.doc).toBeTruthy());
    await waitFor(() => expect(result.current.compile?.ok).toBe(true));
  });

  it("mutate saves with the loaded mtime and recompiles", async () => {
    // getProject returns mtime 1 on first load, then mtime 2 after the post-save
    // invalidate/refetch -- so the compile query's key (["compile", name, mtime])
    // actually changes and a real second compile fires, not just a re-render.
    vi.spyOn(api, "getProject")
      .mockResolvedValueOnce(doc as any)
      .mockResolvedValue({ ...doc, mtime: 2 } as any);
    const compileSpy = vi.spyOn(api, "compile").mockResolvedValue({ ok: true, errors: [],
      manifest: [], unverified: [], size: 10, etag: "e" } as any);
    const save = vi.spyOn(api, "saveProject")
      .mockResolvedValue({ name: "p", mtime: 2 } as any);
    const { result } = renderHook(() => useProject("p"), { wrapper });
    await waitFor(() => expect(result.current.doc).toBeTruthy());
    await waitFor(() => expect(compileSpy).toHaveBeenCalledTimes(1));
    const next = [{ id: "op1", type: "tank_heater_names", params: {},
                    enabled: true, note: "" }];
    await act(async () => { result.current.mutate(next as any); });
    await waitFor(() => expect(save).toHaveBeenCalled());
    const [name, project, mtime] = save.mock.calls[0];
    expect(name).toBe("p");
    expect(project.ops).toEqual(next);
    expect(mtime).toBe(1);
    await waitFor(() => expect(compileSpy).toHaveBeenCalledTimes(2));
  });

  it("surfaces a save failure as a structured error", async () => {
    vi.spyOn(api, "getProject").mockResolvedValue(doc as any);
    vi.spyOn(api, "compile").mockResolvedValue({ ok: true, errors: [], manifest: [],
      unverified: [], size: 10, etag: "e" } as any);
    vi.spyOn(api, "saveProject")
      .mockRejectedValue(new api.ApiError(409, "STALE_SAVE", { message: "changed" }));
    const { result } = renderHook(() => useProject("p"), { wrapper });
    await waitFor(() => expect(result.current.doc).toBeTruthy());
    await act(async () => { result.current.mutate([]); });
    await waitFor(() => expect(result.current.error?.code).toBe("STALE_SAVE"));
  });

  it("surfaces a compile failure as a structured error", async () => {
    // Finding 1: a failed compile must not leave the header on "…" forever with no
    // toast -- the compile query's error has to reach the same `error` surface the
    // save mutation uses.
    vi.spyOn(api, "getProject").mockResolvedValue(doc as any);
    vi.spyOn(api, "compile")
      .mockRejectedValue(new api.ApiError(500, "GATE_FAILED", { message: "gate failed" }));
    const { result } = renderHook(() => useProject("p"), { wrapper });
    await waitFor(() => expect(result.current.doc).toBeTruthy());
    await waitFor(() => expect(result.current.error?.code).toBe("GATE_FAILED"));
  });

  // FINDING 3: `doc.isError` used to reach nothing -- a project that has never been
  // created (the common first-run case) 404s and the app showed a blank shell with no
  // error and no way forward. It now feeds the same `error` surface as save/compile.
  it("surfaces a doc load failure (e.g. a missing project) as a structured error",
     async () => {
    vi.spyOn(api, "getProject").mockRejectedValue(
      new api.ApiError(404, "NOT_FOUND", { message: "no project 'p'" }));
    const { result } = renderHook(() => useProject("p"), { wrapper });
    await waitFor(() => expect(result.current.error?.code).toBe("NOT_FOUND"));
    expect(result.current.doc).toBeUndefined();
  });

  it("clearError drops a stale error immediately", async () => {
    vi.spyOn(api, "getProject").mockResolvedValue(doc as any);
    vi.spyOn(api, "compile")
      .mockRejectedValue(new api.ApiError(500, "GATE_FAILED", { message: "gate failed" }));
    const { result } = renderHook(() => useProject("p"), { wrapper });
    await waitFor(() => expect(result.current.error?.code).toBe("GATE_FAILED"));
    act(() => result.current.clearError());
    await waitFor(() => expect(result.current.error).toBeNull());
  });
});

describe("useProject preview", () => {
  it("setPreview saves the preview without touching the ops", async () => {
    vi.spyOn(api, "getProject").mockResolvedValue(doc as any);
    vi.spyOn(api, "compile").mockResolvedValue({ ok: true, errors: [], manifest: [],
      unverified: [], size: 10, etag: "e" } as any);
    const save = vi.spyOn(api, "saveProject")
      .mockResolvedValue({ name: "p", mtime: 2 } as any);
    const { result } = renderHook(() => useProject("p"), { wrapper });
    await waitFor(() => expect(result.current.doc).toBeTruthy());
    await act(async () => {
      result.current.setPreview({ floorplan: "WD", options: ["Black Tank"] });
    });
    await waitFor(() => expect(save).toHaveBeenCalled());
    const [, project] = save.mock.calls[0];
    expect((project as any).preview).toEqual({ floorplan: "WD",
                                               options: ["Black Tank"] });
    expect((project as any).ops).toEqual([]);
  });

  it("exposes the saved preview", async () => {
    vi.spyOn(api, "getProject").mockResolvedValue({
      ...doc, project: { ...doc.project, preview: { floorplan: "WT" } } } as any);
    vi.spyOn(api, "compile").mockResolvedValue({ ok: true, errors: [], manifest: [],
      unverified: [], size: 10, etag: "e" } as any);
    const { result } = renderHook(() => useProject("p"), { wrapper });
    await waitFor(() => expect(result.current.doc).toBeTruthy());
    expect(result.current.preview).toEqual({ floorplan: "WT" });
  });
});
