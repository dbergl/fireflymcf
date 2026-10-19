// studio/web/src/ExportDialog.test.tsx
import { describe, it, expect, vi, afterEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import ExportDialog from "./ExportDialog";
import * as api from "./api";

const okCompile = { ok: true, errors: [], manifest: ["button: object 1422 on pin 28"],
                    unverified: [], size: 1069860, etag: "e" };

afterEach(() => vi.restoreAllMocks());

describe("ExportDialog", () => {
  it("shows the manifest before exporting", () => {
    render(<ExportDialog name="p" compile={okCompile as any} onClose={vi.fn()} />);
    expect(screen.getByText(/object 1422 on pin 28/)).toBeInTheDocument();
  });

  it("exports and shows filename, size and download link", async () => {
    vi.spyOn(api, "exportBuild").mockResolvedValue({
      filename: "mcf-p.bin", path: "build/mcf-p.bin", size: 1069860,
      sha256: "abc123", etag: "e", manifest: okCompile.manifest, unverified: [],
      download: "/api/projects/p/export/file",
    } as any);
    render(<ExportDialog name="p" compile={okCompile as any} onClose={vi.fn()} />);
    await userEvent.click(screen.getByRole("button", { name: /export/i }));
    await waitFor(() => expect(screen.getByText("mcf-p.bin")).toBeInTheDocument());
    expect(screen.getByText(/abc123/)).toBeInTheDocument();
    const link = screen.getByRole("link", { name: /download/i }) as HTMLAnchorElement;
    expect(link.getAttribute("href")).toBe("/api/projects/p/export/file");
  });

  it("warns about hardware-unverified ops in the build", () => {
    render(<ExportDialog name="p" onClose={vi.fn()}
                         compile={{ ...okCompile, unverified: ["rm"] } as any} />);
    expect(screen.getByText(/hardware-unverified/i)).toBeInTheDocument();
    expect(screen.getByText(/\brm\b/)).toBeInTheDocument();
  });

  it("refuses to export a failing project and shows the errors", async () => {
    const spy = vi.spyOn(api, "exportBuild");
    render(<ExportDialog name="p" onClose={vi.fn()} compile={{
      ok: false, errors: [{ op_id: "a", code: "NO_SUCH_LABEL", message: "not found",
                            offers: [] }],
      manifest: [], unverified: [], size: null, etag: "e" } as any} />);
    expect(screen.getByText("NO_SUCH_LABEL")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /export/i })).toBeDisabled();
    expect(spy).not.toHaveBeenCalled();
  });

  it("surfaces an export failure structurally", async () => {
    vi.spyOn(api, "exportBuild")
      .mockRejectedValue(new api.ApiError(409, "COMPILE_FAILED", { errors: [] }));
    render(<ExportDialog name="p" compile={okCompile as any} onClose={vi.fn()} />);
    await userEvent.click(screen.getByRole("button", { name: /export/i }));
    await waitFor(() =>
      expect(screen.getByText("COMPILE_FAILED")).toBeInTheDocument());
  });

  it("keeps the exported manifest frozen when the live compile changes underneath it",
     async () => {
    const compileA = { ...okCompile, manifest: ["manifest A"] };
    vi.spyOn(api, "exportBuild").mockResolvedValue({
      filename: "mcf-p.bin", path: "build/mcf-p.bin", size: 1,
      sha256: "abc", etag: "e", manifest: ["manifest B"], unverified: [],
      download: "/api/projects/p/export/file",
    } as any);
    const { rerender } = render(
      <ExportDialog name="p" compile={compileA as any} onClose={vi.fn()} />);
    await userEvent.click(screen.getByRole("button", { name: /export/i }));
    await waitFor(() => expect(screen.getByText("manifest B")).toBeInTheDocument());

    const compileC = { ...okCompile, manifest: ["manifest C"] };
    rerender(<ExportDialog name="p" compile={compileC as any} onClose={vi.fn()} />);
    expect(screen.getByText("manifest B")).toBeInTheDocument();
    expect(screen.queryByText("manifest C")).not.toBeInTheDocument();
  });

  it("disables export and shows no error list while compile is still loading",
     () => {
    render(<ExportDialog name="p" compile={undefined} onClose={vi.fn()} />);
    expect(screen.getByRole("button", { name: /export/i })).toBeDisabled();
    expect(screen.queryByText(/does not compile/i)).not.toBeInTheDocument();
    expect(screen.getByText(/compiling/i)).toBeInTheDocument();
  });

  it("clears a stale success block before a failing re-export", async () => {
    const spy = vi.spyOn(api, "exportBuild");
    spy.mockResolvedValueOnce({
      filename: "mcf-p.bin", path: "build/mcf-p.bin", size: 1,
      sha256: "abc", etag: "e", manifest: okCompile.manifest, unverified: [],
      download: "/api/projects/p/export/file",
    } as any);
    render(<ExportDialog name="p" compile={okCompile as any} onClose={vi.fn()} />);
    await userEvent.click(screen.getByRole("button", { name: /export/i }));
    await waitFor(() => expect(screen.getByText("mcf-p.bin")).toBeInTheDocument());

    spy.mockRejectedValueOnce(new api.ApiError(409, "COMPILE_FAILED", { errors: [] }));
    await userEvent.click(screen.getByRole("button", { name: /export/i }));
    await waitFor(() =>
      expect(screen.getByText("COMPILE_FAILED")).toBeInTheDocument());
    expect(screen.queryByText("mcf-p.bin")).not.toBeInTheDocument();
  });
});

it("echoes the build name the coach's boot menu will show", () => {
  render(<ExportDialog name="p" compile={okCompile as any} onClose={vi.fn()}
                       buildName="Jayco B-Van V13 (Custom)" />);
  expect(screen.getByText(/boot menu/i)).toBeInTheDocument();
  expect(screen.getByText("Jayco B-Van V13 (Custom)")).toBeInTheDocument();
});

it("says so when no build name is set, rather than showing nothing", () => {
  render(<ExportDialog name="p" compile={okCompile as any} onClose={vi.fn()}
                       buildName={null} />);
  expect(screen.getByText(/keeps the stock name/i)).toBeInTheDocument();
});
