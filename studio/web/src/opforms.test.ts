import { describe, it, expect } from "vitest";
import * as f from "./opforms";
import type { OpSchema } from "./api";

const schema = (properties: any, required: string[] = [],
                doc = "does a thing", hardware_verified = true): OpSchema =>
  ({ params: { properties, required }, doc, hardware_verified });

const schemas: Record<string, OpSchema> = {
  rename_label: schema({ object: { type: "integer" } }, ["object"]),
  add_button: schema({ channel: { type: "integer" } }, ["channel"]),
  tank_heater_names: schema({}, [], "Widen the tank-heater whitelists"),
  move_attach: schema({
    page: { type: ["string", "integer"], description: "page name or index" },
    child: { type: ["integer", "object"], description: "object index or ref" },
    x: { type: ["integer", "null"], default: null },
    y: { type: ["integer", "null"], default: null },
  }, ["page", "child"], "Move a control's attach point"),
  install_glyph: schema({
    slot: { type: "integer" },
    rows: { type: "array", items: { type: "string" } },
  }, ["slot", "rows"], "Install an authored glyph"),
  // A hypothetical future op carrying pixel data. install_glyph used to serve as this
  // fixture, but it is CURATED now (ImportGlyph.tsx collects its `rows` from artwork),
  // so the rule it demonstrates -- a generated form must never pretend it can collect
  // an array of strings from a text box -- needs an uncurated op of its own to stand on.
  paint_region: schema({
    object: { type: "integer" },
    rows: { type: "array", items: { type: "string" } },
  }, ["object", "rows"], "Paint a region from pixel rows"),
};

describe("uncuratedOps", () => {
  it("lists only ops with no hand-written control", () => {
    const types = f.uncuratedOps(schemas).map((o) => o.type);
    expect(types).not.toContain("rename_label");
    expect(types).not.toContain("add_button");
    // Curated because they now have cards of their own: install_glyph is collected by
    // the artwork importer, and add_glyph_button is only ever offered paired with the
    // wire_button the engine requires of it.
    expect(types).not.toContain("install_glyph");
    expect(types).not.toContain("add_glyph_button");
    expect(types).toEqual(
      expect.arrayContaining(["tank_heater_names", "move_attach", "paint_region"]));
  });

  it("surfaces a brand-new engine op with no front-end change — the whole point", () => {
    const withNew = { ...schemas,
      brand_new_op: schema({ n: { type: "integer" } }, ["n"], "Something new") };
    const found = f.uncuratedOps(withNew).find((o) => o.type === "brand_new_op");
    expect(found).toBeTruthy();
    expect(found!.doc).toBe("Something new");
    expect(found!.unsupported).toBeUndefined();
  });

  it("marks an op the generic form cannot honestly render, with a reason", () => {
    const pixels = f.uncuratedOps(schemas).find((o) => o.type === "paint_region")!;
    expect(pixels.unsupported).toMatch(/rows/i);
  });

  it("carries the hardware-unverified flag through", () => {
    const withUnverified = { ...schemas,
      risky: schema({}, [], "Risky", false) };
    expect(f.uncuratedOps(withUnverified)
      .find((o) => o.type === "risky")!.hardwareVerified).toBe(false);
  });

  it("sorts by type so the catalog order is stable", () => {
    const types = f.uncuratedOps(schemas).map((o) => o.type);
    expect(types).toEqual([...types].sort());
  });
});

describe("fieldsOf", () => {
  it("derives a field per property, marking required ones", () => {
    const fields = f.fieldsOf(schemas.move_attach);
    expect(fields.map((x) => x.name)).toEqual(["page", "child", "x", "y"]);
    expect(fields.find((x) => x.name === "page")!.required).toBe(true);
    expect(fields.find((x) => x.name === "x")!.required).toBe(false);
  });

  it("maps each declared type to a control kind", () => {
    const fields = f.fieldsOf(schema({
      n: { type: "integer" }, s: { type: "string" }, b: { type: "boolean" },
      opt: { type: ["integer", "null"] },
      pick: { type: "string", enum: ["a", "b"] },
      rgb: { type: "array", items: { type: "integer" }, minItems: 3, maxItems: 3 },
      weird: { type: "array", items: { type: "string" } },
    }));
    const kind = (n: string) => fields.find((x) => x.name === n)!.kind;
    expect(kind("n")).toBe("integer");
    expect(kind("s")).toBe("string");
    expect(kind("b")).toBe("boolean");
    expect(kind("opt")).toBe("integer");
    expect(kind("pick")).toBe("enum");
    expect(kind("rgb")).toBe("rgb");
    expect(kind("weird")).toBe("unsupported");
  });

  it("treats a string-or-integer union as text — a page is named or numbered", () => {
    expect(f.fieldsOf(schemas.move_attach)
      .find((x) => x.name === "page")!.kind).toBe("string");
  });

  it("carries defaults and descriptions", () => {
    const donor = f.fieldsOf(schema({
      donor: { type: "integer", default: 247, description: "object to clone" } }));
    expect(donor[0].default).toBe(247);
    expect(donor[0].description).toBe("object to clone");
  });

  it("yields nothing for a no-parameter op", () => {
    expect(f.fieldsOf(schemas.tank_heater_names)).toEqual([]);
  });
});

describe("paramsFrom", () => {
  const fields = f.fieldsOf(schemas.move_attach);

  it("coerces by kind and omits untouched optionals", () => {
    const out = f.paramsFrom(fields, { page: "home", child: "42", x: "10", y: "" });
    expect(out).toEqual({ page: "home", child: 42, x: 10 });
  });

  it("keeps a numeric-looking page as text when the schema allows either", () => {
    // `page` is string|integer: "4" is a legitimate object index typed by hand, but
    // the engine resolves either, so the client must not guess. Text wins.
    expect(f.paramsFrom(fields, { page: "4", child: "1" }).page).toBe("4");
  });

  it("passes a boolean straight through and omits a false one only if it defaults false",
     () => {
    const bf = f.fieldsOf(schema({ flag: { type: "boolean", default: false } }));
    expect(f.paramsFrom(bf, { flag: true })).toEqual({ flag: true });
    expect(f.paramsFrom(bf, { flag: false })).toEqual({});
  });

  it("builds an rgb triple from three numbers", () => {
    const rf = f.fieldsOf(schema({
      c: { type: "array", items: { type: "integer" }, minItems: 3, maxItems: 3 } }));
    expect(f.paramsFrom(rf, { c: "65,81,163" })).toEqual({ c: [65, 81, 163] });
  });
});

describe("isComplete", () => {
  const fields = f.fieldsOf(schemas.move_attach);

  it("is false until every required field has a value", () => {
    expect(f.isComplete(fields, { page: "home" })).toBe(false);
    expect(f.isComplete(fields, { page: "home", child: "42" })).toBe(true);
  });

  it("does not require the optionals", () => {
    expect(f.isComplete(fields, { page: "home", child: "42", x: "", y: "" })).toBe(true);
  });

  it("is true immediately for a no-parameter op", () => {
    expect(f.isComplete(f.fieldsOf(schemas.tank_heater_names), {})).toBe(true);
  });
});
