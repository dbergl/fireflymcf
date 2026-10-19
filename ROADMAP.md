# Roadmap

This repository becomes the public home of the `fireflymcf` library, the owner-facing docs and the
Claude Code plugin. The research repository (corpus, captures, Ghidra projects, the reference
coach's builds) stays private and depends on this one.

## 1. Split from the research repository

- [x] Move `tools/fireflymcf/` (library + CLI) to `src/fireflymcf/`, imported as `fireflymcf`, with
      every test. Corpus-backed tests run against research data linked in by
      `scripts/link-research.sh`, and are not collected without it. Left behind: the tests of
      `g12flash`, `tankpatch`, `lyra_usbcan`, `mira_config` (research-repo tools) and of Studio.
- [x] Move every prose doc and research notebook, with module paths rewritten. The design history
      and the build log went back to the research repository before going public. Generated docs regenerate
      identically. New `CLAUDE.md` for this repo: the research one's rules, traps and capability
      table, without the coach's build state.
- [x] Move the owner tools built on the library: `tankpatch` (`fireflymcf-tankpatch`) and
      `g12flash` (`fireflymcf-g12flash`).
- [x] Move `studio/` (the browser editor); `pip install -e '.[studio]'`. Not packaged in the wheel.
- [x] Move `pyproject.toml`; set `license = "MIT"`.
- [x] Research repo installs this one (`pip install -e ../fireflymcf`), rewrites `tools.fireflymcf` imports to
      `fireflymcf`, and drops its copies (2026-10-16, branch `use-fireflymcf`). `tests/answer_key.py`
      stays there with the captures and is linked in.
- [x] Reference-coach defaults: checked 2026-10-16, there are none in this repo's general path.
      `tank_heater_names` applies only when a recipe names it (its purpose states the SY/WT
      trade); renders use no floorplan unless `--config` gives one (`coach-config`).
- [x] `RECIPE-OPS.md` no longer reads the reference coach's recipe: its "in v26" column is now
      each op's `hardware_verified` default, and statuses are current through v50.

## 2. Make the tool agent-friendly

- [x] `--json` on the commands an agent drives: `labels`, `validate`, `compile`, `coverage`,
      `identify`, `image-export` join `doctor`, `pages`, `options`, `pins`, `gestures`, `ops`,
      `verify` (render placements were already JSON). A failure under `--json` is JSON on stdout.
      Not yet: `g12-rules`, and the writers (`patch`, `swap-image`, `extract`, `regions`).
- [x] A JSON Schema for `.ffproj.json` (`schema/ffproj.schema.json`, `fireflymcf schema`), generated
      from each op's `PARAMS`; all 28 reference recipes satisfy it. The engine now refuses an
      op param it does not declare (a typo used to be ignored).
- [x] `fireflymcf ops [NAME…] [--json]`: the op reference from the installed package.
- [ ] `fireflymcf ops --for MCF`: each op as available, refused (with reason), or unverified
      on this build.
- [x] `fireflymcf verify BUILD STOCK [--config CFG]…`: sections, every differing byte run by
      region (same-size builds), GuiData objects changed/added, and every page rendered from both
      with the changed area; exit 1 on a bad checksum, an unexplained run, a failed render or a
      stock build name (`buildcheck.py`).
- [ ] `verify --project P`: match each changed object and page to the op that declared it, and
      flag the rest.
- [x] Conservative default: `compile` refuses a project with an unverified op unless
      `--allow-unverified` (13.6-only ops already refuse other builds by their byte checks).

## Gaps found by the skill's test runs (2026-10-16)

- [x] `rename_label` / `patch --rename` crashed on text spanning lines (`'Water\nPump'`).
- [x] `add_button` / `wire_button` refuse an output the G12 OUTPUTS list names (`NAMED_OUTPUT`,
      `allow_named_output`; `outputs.py`).
- [x] `recolor` refuses a card of controls (`CONTAINER`, `allow_container`). `patch --recolor`
      does not check yet.
- [x] `labels --containing "PORCH"` failed as "ambiguous": a nested length-byte tie, now
      settled by the record decoder.

## 3. The plugin

- [x] `build-firmware` skill, condensed from `AGENT-GUIDE`, with a fixed handover checklist;
      baseline and with-skill runs on a four-request owner scenario.
- [x] Slash commands `/fireflymcf:inspect`, `/fireflymcf:build`.
- [x] Coach profile: `fireflymcf coach-config` (named floorplan/options, checked against the build,
      or a `candump -L` capture decoded by `livecfg`) and `/fireflymcf:coach-profile`.
- [x] Flash report: `/fireflymcf:flash-report` and `.github/ISSUE_TEMPLATE/flash-report.md`.
- [ ] Run each command once in a fresh Claude Code session with the plugin installed from the
      marketplace (`/plugin marketplace add`), not only from the checkout.

## 4. Later

- [ ] A thin MCP server over the same library, for Claude Desktop / claude.ai users without a shell:
      inspect, render (returning PNGs), validate, compile.

## Before going public

- [x] Scrub the LAN host and local home paths (2026-10-16): `pi@<gateway>`, `$GHIDRA_HOME`,
      `$SCRATCH`, relative paths. None left in the tree.
- [ ] **Replace the git history before flipping public.** History still holds the LAN host and,
      from the first import, the full `tests/answer_key.py` (the captures transcribed). A
      single fresh commit of the scrubbed tree (orphan branch), or `git filter-repo`.
- [x] Owner's review (2026-10-16): the build log, the design history with its panel mockups,
      and the tests that pin the reference coach's builds moved to the research repository;
      personal names replaced; the panel's serial and asset numbers removed; `BUS-BASELINE`
      checked for location, network names and personal details (none).
- [x] README: corrected install commands; disclaimer drafted (owner to approve the wording).
- [ ] Flip the repo to public; the install line then works for anyone.
