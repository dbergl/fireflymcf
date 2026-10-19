// studio/web/e2e/happy.spec.ts
// One path through the whole stack: create a project, rename a label on HOME, watch
// the canvas re-render, export the build. Everything below the UI is the real engine.
//
// FINDING 8 fix: selection used to be best-effort (a try/catch around the click, the
// rename itself gated on `if (await label.count())`), so "rename -> canvas updates"
// -- the test's own headline claim -- could be silently skipped and the test would
// still go green. Selection is now deterministic: fetch the real placements for HOME
// and click the CENTRE of a known label's own box. Canvas's click handler computes
// the hit via `selectAt(placements, x, y)` against the fetched coordinates -- not via
// whichever overlapping SVG rect the browser's real hit-test happens to land on -- so
// this does not need `force` and cannot be obscured.
import { test, expect } from "@playwright/test";
// Reused, not reimplemented: the real selection algorithm the running app uses to
// resolve a click, so "would this click actually pick this object" is answered by
// the same code the browser runs, not a hand-rolled approximation of it.
import { selectAt } from "../src/selection";

test("open → rename → canvas updates → export", async ({ page, request }) => {
  const name = `e2e${Date.now()}`;
  const created = await request.post("/api/projects", { data: { name } });
  expect(created.ok()).toBeTruthy();

  await page.goto(`/?project=${name}`);
  await expect(page.getByText("Firefly Studio")).toBeVisible();
  await expect(page.getByText("compiled ✓")).toBeVisible({ timeout: 60_000 });

  // The canvas renders the HOME page.
  const img = page.locator(".stage img");
  await expect(img).toHaveAttribute("src", new RegExp(`/render/home`));

  // Find a caption's own box from the real placements endpoint -- the same data
  // Canvas's overlay is built from -- so the click coordinates are known good rather
  // than guessed from a screenshot. A control like Water Pump has TWO wtype-11
  // "label" placements sharing one obj_index (its icon glyph AND its text caption),
  // and the Inspector resolves `caption` as the FIRST label match in that object's
  // group -- so which specific record the click landed inside doesn't decide what
  // text shows. Picking an object with EXACTLY ONE label placement (no icon sharing
  // its obj_index) makes the caption unambiguous once we're sure the click actually
  // lands on IT and not some other, smaller, overlapping object -- checked below via
  // the app's own `selectAt`, not assumed.
  const placementsRes = await request.get(`/api/projects/${name}/placements/home`);
  expect(placementsRes.ok()).toBeTruthy();
  const placementsBody = await placementsRes.json();
  const labelCountByObj = new Map<number, number>();
  for (const p of placementsBody.placements) {
    if (p.kind === "label") {
      labelCountByObj.set(p.obj_index, (labelCountByObj.get(p.obj_index) ?? 0) + 1);
    }
  }
  const candidates = placementsBody.placements.filter((p: any) =>
    p.kind === "label" && typeof p.label === "string" && p.label.trim().length > 3
    && p.editable.includes("rename_label") && labelCountByObj.get(p.obj_index) === 1);
  const target = candidates.find((p: any) => {
    const [cx0, cy0, cx1, cy1] = p.box;
    const cx = (cx0 + cx1) / 2, cy = (cy0 + cy1) / 2;
    return selectAt(placementsBody, cx, cy)?.obj_index === p.obj_index;
  });
  expect(target, "HOME must place at least one unambiguous (single-caption) "
    + "rename_label-editable object whose own box centre selects it").toBeTruthy();
  const stockLabel: string = target.label;

  const [x0, y0, x1, y1] = target.box;
  const svg = page.locator(".stage svg");
  await svg.click({ position: { x: (x0 + x1) / 2, y: (y0 + y1) / 2 } });

  // Selection is unconditional now: the inspector MUST offer a label field for the
  // object we just picked, carrying that object's own stock text.
  const label = page.getByLabel(/^label$/i);
  await expect(label).toBeVisible();
  await expect(label).toHaveValue(stockLabel);
  await label.fill("E2E");
  await page.getByRole("button", { name: /apply label/i }).click();
  await expect(page.locator(".changelist li")).toHaveCount(1);

  // Export.
  await page.getByRole("button", { name: /export/i }).click();
  await page.getByRole("button", { name: /^export$/i }).click();
  await expect(page.getByText(`mcf-${name}.bin`)).toBeVisible({ timeout: 120_000 });
  await expect(page.getByRole("link", { name: /download/i })).toBeVisible();
});
