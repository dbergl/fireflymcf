// studio/web/src/api.test.ts
import { describe, it, expect, vi, afterEach } from "vitest";
import * as api from "./api";

const okJson = (body: unknown, status = 200) =>
  Promise.resolve(new Response(JSON.stringify(body), {
    status, headers: { "Content-Type": "application/json" },
  }));

afterEach(() => vi.restoreAllMocks());

describe("api", () => {
  it("getProject returns the parsed document", async () => {
    const doc = { name: "p", mtime: 1, project: { schema: 1, base_sha256: "a",
                                                  build_name: null, ops: [] } };
    vi.stubGlobal("fetch", vi.fn(() => okJson(doc)));
    expect(await api.getProject("p")).toEqual(doc);
    expect(fetch).toHaveBeenCalledWith("/api/projects/p", expect.anything());
  });

  it("saveProject sends project and expected_mtime", async () => {
    vi.stubGlobal("fetch", vi.fn(() => okJson({ name: "p", mtime: 2 })));
    const project = { schema: 1, base_sha256: "a", build_name: null, ops: [] };
    await api.saveProject("p", project, 1);
    const [, init] = (fetch as any).mock.calls[0];
    expect(init.method).toBe("PUT");
    expect(JSON.parse(init.body)).toEqual({ project, expected_mtime: 1 });
  });

  it("throws ApiError carrying the structured code, never a parsed string", async () => {
    vi.stubGlobal("fetch", vi.fn(() =>
      okJson({ detail: { code: "STALE_SAVE", message: "changed on disk" } }, 409)));
    const err = await api.getProject("p").catch((e) => e);
    expect(err).toBeInstanceOf(api.ApiError);
    expect(err.status).toBe(409);
    expect(err.code).toBe("STALE_SAVE");
    expect(err.detail.message).toBe("changed on disk");
  });

  it("ApiError falls back to a code when the body is not structured", async () => {
    vi.stubGlobal("fetch", vi.fn(() =>
      Promise.resolve(new Response("boom", { status: 500 }))));
    const err = await api.getProject("p").catch((e) => e);
    expect(err.status).toBe(500);
    expect(err.code).toBe("UNKNOWN");
  });

  it("renderUrl encodes knobs and omits empty ones", () => {
    expect(api.renderUrl("p", "home", {})).toBe("/api/projects/p/render/home");
    const u = api.renderUrl("p", "home", { tank_level: 0.5, slots: "5:8" });
    expect(u).toContain("tank_level=0.5");
    expect(u).toContain("slots=5%3A8");
  });

  it("encodes project names in paths", () => {
    expect(api.renderUrl("a b", "home", {})).toBe("/api/projects/a%20b/render/home");
  });

  it("renderUrl keeps an explicit tank_level of 0, not treating it as empty", () => {
    expect(api.renderUrl("p", "home", { tank_level: 0 })).toContain("tank_level=0");
  });

  it("renderUrl with two different etags produces two different URLs", () => {
    // The etag is a pure cache-buster (App.tsx feeds it the compile etag) -- without a
    // string difference in the URL, React's DOM diff no-ops the <img> src write and the
    // browser never re-fetches after an edit changes the compiled bytes.
    const a = api.renderUrl("p", "home", { etag: "e1" });
    const b = api.renderUrl("p", "home", { etag: "e2" });
    expect(a).not.toBe(b);
    expect(a).toContain("etag=e1");
    expect(b).toContain("etag=e2");
  });

  it("getHealth calls /api/health and returns the parsed body", async () => {
    const body = { status: "ok", stock_build_name: "Jayco B-Van V13",
                   base_sha256: "deadbeef" };
    vi.stubGlobal("fetch", vi.fn(() => okJson(body)));
    expect(await api.getHealth()).toEqual(body);
    expect(fetch).toHaveBeenCalledWith("/api/health", expect.anything());
  });

  it("resolves rather than throwing on an empty-body success (204)", async () => {
    vi.stubGlobal("fetch", vi.fn(() =>
      Promise.resolve(new Response(null, { status: 204 }))));
    await expect(api.getProject("p")).resolves.toBeUndefined();
  });
});
