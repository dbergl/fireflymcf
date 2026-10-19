// studio/web/src/opforms.ts
// Generic op forms, derived from `/api/schemas`.
//
// This is deliberately NOT how the main editing controls work. A schema says
// `{"type": "integer"}`; it cannot say "this pin is already commanded by another
// control, tick to confirm", or "hold 3 s to toggle" instead of
// `kind: "hold_to_toggle"`, or that a longer label turns a same-size edit into a
// relocating build. Those affordances are the safety surface of this editor and they
// are hand-written, per op, on purpose.
//
// What a generated form is for is the OTHER gap: an op the front end has never heard
// of is invisible and unusable, with no fallback at all. Everything here exists so a
// new engine op is reachable the day it lands — plainly, but reachable — and so the
// three that already have no control (add_glyph_button, move_attach,
// tank_heater_names) stop being unreachable.
import type { OpSchema } from "./api";

// Op types with a hand-written control somewhere in the editor. Subtracting this from
// `/api/schemas` is what makes a newly-registered engine op appear in the catalogue
// with no front-end change; adding a type here is the one-line move when someone later
// gives it a curated control of its own.
//
// `add_glyph_button` and `install_glyph` moved here once they got cards. Both were
// listed as gaps a generated form could not honestly cover: install_glyph's `rows` is
// pixel data, and add_glyph_button on its own compiles to an image the engine refuses
// (an unwired shell still drives its donor's channel), which left the editor stuck --
// see GlyphButton.tsx.
export const CURATED = [
  "add_button",      // the guided add-a-button card
  "add_glyph_button",// the glyph-button card — always paired with its wire_button
  "install_glyph",   // the artwork importer (a generic form cannot collect pixels)
  "move_element",    // canvas drag, and the inspector's geometry edits
  "recolor",         // inspector: colour picker over the byte-derived palette
  "remap_channel",   // inspector: pin picker, warnings, acknowledgement
  "remove_control",  // inspector: remove, with the hardware-unverified warning
  "rename_label",    // inspector: label field, with the grow consent
  "set_font",        // inspector: font slot + specimen
  "set_gesture",     // inspector: plain-language gesture control
  "set_icon",        // inspector: icon font + character + glyph-only
  "wire_button",     // inspector: wire to a pin (indicator + command)
] as const;

export type FieldKind =
  | "integer" | "string" | "boolean" | "enum" | "rgb" | "unsupported";

export type Field = {
  name: string;
  kind: FieldKind;
  required: boolean;
  default?: unknown;
  description?: string;
  options?: string[];
};

export type CatalogEntry = {
  type: string;
  doc: string;
  hardwareVerified: boolean;
  /** Set when a generic form cannot honestly collect this op's params. */
  unsupported?: string;
};

const types = (prop: any): string[] =>
  Array.isArray(prop?.type) ? prop.type : prop?.type ? [prop.type] : [];

function kindOf(prop: any): FieldKind {
  const t = types(prop);
  if (prop?.enum) return "enum";
  if (t.includes("array")) {
    // The only array shape a form can honestly render is a fixed numeric tuple, which
    // in this engine means an [r, g, b] colour. An array of strings (install_glyph's
    // hex pixel rows) is data that belongs in an importer, not a text box.
    return prop.items?.type === "integer" && prop.maxItems === 3
      ? "rgb" : "unsupported";
  }
  // A string|integer union (a page: named or by index) is text — the engine resolves
  // either, so the client must not guess which the user meant.
  if (t.includes("string")) return "string";
  if (t.includes("integer") || t.includes("number")) return "integer";
  if (t.includes("boolean")) return "boolean";
  // An integer|object union is a target: an index, or {"ref": op_id}. The generic form
  // offers the index; a ref is something only the curated flows can produce.
  if (t.includes("object")) return "integer";
  return "unsupported";
}

export function fieldsOf(schema: OpSchema): Field[] {
  const props = schema.params?.properties ?? {};
  const required = schema.params?.required ?? [];
  return Object.entries(props).map(([name, prop]: [string, any]) => ({
    name,
    kind: kindOf(prop),
    required: required.includes(name),
    ...(prop && "default" in prop ? { default: prop.default } : {}),
    ...(prop?.description ? { description: prop.description } : {}),
    ...(prop?.enum ? { options: prop.enum as string[] } : {}),
  }));
}

export function uncuratedOps(schemas: Record<string, OpSchema>): CatalogEntry[] {
  return Object.entries(schemas)
    .filter(([type]) => !(CURATED as readonly string[]).includes(type))
    .map(([type, schema]) => {
      const blocked = fieldsOf(schema).filter((x) => x.kind === "unsupported");
      return {
        type,
        doc: schema.doc || type,
        hardwareVerified: schema.hardware_verified !== false,
        ...(blocked.length
          ? { unsupported: `needs an importer for ${blocked
              .map((x) => x.name).join(", ")} — not something a text field can collect` }
          : {}),
      };
    })
    .sort((a, b) => a.type.localeCompare(b.type));
}

/** Raw form state: every control holds a string, except booleans. */
export type Values = Record<string, string | boolean>;

export function initialValues(fields: Field[]): Values {
  const out: Values = {};
  for (const f of fields) {
    if (f.kind === "boolean") out[f.name] = f.default === true;
    else if (f.default !== undefined && f.default !== null) {
      out[f.name] = String(f.default);
    } else out[f.name] = "";
  }
  return out;
}

export function isComplete(fields: Field[], values: Values): boolean {
  return fields.every((f) => {
    if (!f.required) return true;
    const v = values[f.name];
    return typeof v === "boolean" ? true : String(v ?? "").trim() !== "";
  });
}

/**
 * Build the op's params. Only what the user actually supplied is sent: an untouched
 * optional is omitted rather than sent as null, so the engine applies its own default
 * and the client never has to know what that default is.
 */
export function paramsFrom(fields: Field[], values: Values): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  for (const f of fields) {
    const raw = values[f.name];
    if (f.kind === "boolean") {
      // A false that matches the schema's own default carries no information.
      if (raw === true) out[f.name] = true;
      else if (raw === false && f.default === true) out[f.name] = false;
      continue;
    }
    const s = String(raw ?? "").trim();
    if (s === "") continue;
    if (f.kind === "integer") {
      const n = Number(s);
      if (!Number.isNaN(n)) out[f.name] = n;
      continue;
    }
    if (f.kind === "rgb") {
      const parts = s.split(",").map((x) => Number(x.trim()));
      if (parts.length === 3 && parts.every((n) => !Number.isNaN(n))) {
        out[f.name] = parts;
      }
      continue;
    }
    out[f.name] = s;
  }
  return out;
}
