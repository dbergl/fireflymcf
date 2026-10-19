// studio/web/src/panes/CoachConfig.tsx
//
// The coach-config picker: which floorplan and options the CANVAS renders under.
//
// This is preview state, never build input. Floorplan and option state lives in G12
// NVM and reaches the panel over the bus (MCF-FORMAT.md §10), so it cannot be a
// property of the file being compiled -- it is persisted in the project only so you
// do not retype it every session, and the compile etag deliberately excludes it.
//
// Options sharing one value slot are a RADIO, not checkboxes: AGS / AES / AES Single
// Output all bind slot 37 and the firmware tells them apart by the value written
// (options._option_value). Rendering them as three booleans is what silently displayed
// "AES Single Output" as AGS. The slot arithmetic itself stays server-side; this
// component only ever names options.
import type { CoachConfigData, LiveImport, Preview } from "../api";
import ImportLive from "./ImportLive";

type Props = {
  config: CoachConfigData;
  preview: Preview;
  onChange: (next: Preview) => void;
  // Optional: without it the import affordance simply is not shown, and the picker
  // works exactly as before. Not every coach has a bridge to capture from.
  onImportLive?: (capture: string) => Promise<LiveImport>;
};

const NONE = "— none —";

export default function CoachConfig(p: Props) {
  const chosen = p.preview.options ?? [];
  const floorplan = p.preview.floorplan ?? "";

  // A slot bound by more than one option name is a radio group. Structural, so it needs
  // no list of which options happen to be generator modes.
  const bySlot = new Map<number, typeof p.config.options>();
  for (const o of p.config.options) {
    bySlot.set(o.slot, [...(bySlot.get(o.slot) ?? []), o]);
  }
  const groups = [...bySlot.values()];
  const booleans = groups.filter((g) => g.length === 1).map((g) => g[0]);
  const radios = groups.filter((g) => g.length > 1);

  const valueOf = (code: string) =>
    p.config.floorplans.find((f) => f.code === code)?.value;

  const defaultsFor = (code: string): string[] | null => {
    const v = valueOf(code);
    if (v === undefined) return null;
    const d = p.config.defaults[String(v)];
    if (!d) return null;                      // no tap handler emits this one
    return p.config.options.filter((o) => d[o.name]).map((o) => o.name);
  };

  const pickFloorplan = (code: string) => {
    const defs = defaultsFor(code);
    // Picking a floorplan REPLACES the selection with that floorplan's defaults. When a
    // floorplan has no stored defaults the options are left exactly as they were, rather
    // than cleared -- clearing would look like a decision the file had made.
    p.onChange({ floorplan: code, options: defs ?? chosen });
  };

  // extra_slots must survive every other edit: it carries the slot-group selections,
  // which are independent of the floorplan and the options.
  const keep = p.preview.extra_slots;

  const toggle = (name: string) => {
    p.onChange({
      floorplan: p.preview.floorplan,
      extra_slots: keep,
      options: chosen.includes(name)
        ? chosen.filter((n) => n !== name)
        : [...chosen, name],
    });
  };

  const pickSlot = (slot: number, value: number) => {
    p.onChange({ ...p.preview,
                 extra_slots: { ...(keep ?? {}), [String(slot)]: value } });
  };

  const pickRadio = (group: typeof p.config.options, name: string | null) => {
    const names = new Set(group.map((o) => o.name));
    const kept = chosen.filter((n) => !names.has(n));
    p.onChange({
      floorplan: p.preview.floorplan,
      extra_slots: keep,
      options: name === null ? kept : [...kept, name],
    });
  };

  const noDefaults = floorplan !== "" && defaultsFor(floorplan) === null;

  // What else a setting drives. The panel derives some display state from a config
  // value rather than receiving it -- the HOME battery block is picked from the
  // generator mode, the left icon column from that plus two more -- so it is not a
  // separate control, and saying so here is what stops someone hunting for one. The
  // server supplies the linkage; the arithmetic never leaves it.
  const drivesFor = (slot: number) =>
    (p.config.derives ?? []).filter((d) => d.source_slot === slot);

  return (
    <div className="coach-config">
      <label>floorplan
        <select value={floorplan}
                onChange={(e) => pickFloorplan(e.target.value)}>
          <option value="">— none —</option>
          {p.config.floorplans.map((f) => (
            <option key={f.value} value={f.code}>{f.code}</option>
          ))}
        </select>
      </label>

      {noDefaults && (
        <p className="hint">
          {floorplan} has no stored defaults — no handler in this build emits them,
          so the options below are left as they were.
        </p>
      )}

      <fieldset>
        <legend>options</legend>
        {booleans.map((o) => (
          <div key={o.name}>
            <label>
              <input type="checkbox" checked={chosen.includes(o.name)}
                     onChange={() => toggle(o.name)} />
              {o.name}
            </label>
            {drivesFor(o.slot).map((d) => (
              <p key={d.what} className="hint">
                Also sets {d.what}. {d.detail}
              </p>
            ))}
          </div>
        ))}
      </fieldset>

      {p.onImportLive && (
        <ImportLive onImport={p.onImportLive} onApply={(pv) => p.onChange(pv)} />
      )}

      {(p.config.slot_groups ?? []).map((g) => {
        // an unset slot is 0 -- which is the first choice ("1" battery). Established on
        // the coach: 195FCE9C selector D8 read 00 with "1" selected, 01 with "2".
        const cur = Number(keep?.[String(g.slot)] ?? 0);
        // A control the file describes but no page places is still worth listing --
        // it exists, and hiding it would only move the question -- but it must not look
        // like a setting that does something here. 13.6's 'Battery' group (object 1121)
        // is one: nothing in the whole object table places it. Saying so is the fix for
        // an owner toggling it and finding no effect anywhere.
        const nowhere = g.page === null;
        return (
          <fieldset key={g.slot} className={nowhere ? "unplaced" : undefined}>
            <legend>{g.label}</legend>
            {g.choices.map((c) => (
              <label key={c.value}>
                <input type="radio" name={`group-${g.slot}`} value={c.label}
                       disabled={nowhere} checked={cur === c.value}
                       onChange={() => pickSlot(g.slot, c.value)} />
                {c.label}
              </label>
            ))}
            <p className="hint">
              {nowhere
                ? "Not on any page the preview can draw — nothing here changes."
                : `Shown on the ${g.page} page.`}
            </p>
          </fieldset>
        );
      })}

      {radios.map((group) => {
        const selected = chosen.find((n) => group.some((o) => o.name === n));
        const drives = drivesFor(group[0].slot);
        return (
          <fieldset key={group[0].slot}>
            <legend>generator mode</legend>
            <label>
              <input type="radio" name={`slot-${group[0].slot}`} value={NONE}
                     checked={selected === undefined}
                     onChange={() => pickRadio(group, null)} />
              {NONE}
            </label>
            {group.map((o) => (
              <label key={o.name}>
                <input type="radio" name={`slot-${group[0].slot}`} value={o.name}
                       checked={selected === o.name}
                       onChange={() => pickRadio(group, o.name)} />
                {o.name}
              </label>
            ))}
            {drives.map((d) => (
              <p key={d.what} className="hint">
                Also sets {d.what}. {d.detail}
              </p>
            ))}
          </fieldset>
        );
      })}
    </div>
  );
}
