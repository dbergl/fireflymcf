# fireflymcf

Tools and a Claude Code plugin for reading, rendering and safely modifying **Firefly Integrations
MCF** firmware: the configuration bundle (`mcf.bin`) for the Firefly G12 / Spyder panel RV
multiplex system found in Jayco and other coaches.

> **Status: early.** One coach (a Jayco on firmware 13.6) has run every build the tools have made;
> other coaches and firmware versions are where the work is least proven. See
> [`ROADMAP.md`](ROADMAP.md).

Start with [`docs/GETTING-STARTED.md`](docs/GETTING-STARTED.md) if you own a coach,
[`docs/CHANGE-CATALOG.md`](docs/CHANGE-CATALOG.md) for what a build can change, and
[`docs/MCF-FORMAT.md`](docs/MCF-FORMAT.md) for the format itself.

## What is (and isn't) here

- The code, docs and recipe examples, under the MIT license.
- **No firmware.** You bring your own coach's stock `mcf.bin` and `cli.bin` from Firefly. Firmware
  images, and builds made from them, are never committed here (`.gitignore` blocks `*.bin`).

## Claude Code plugin

At the prompt of a Claude Code session:

```
/plugin marketplace add dbergl/fireflymcf
/plugin install fireflymcf@fireflymcf
```

The plugin drives the `fireflymcf` command-line tool and installs it itself. At each session start
its hook (`plugin/hooks/hooks.json`, `plugin/scripts/session-start.sh`) checks that the release
matching the plugin's version is installed in a venv in the plugin's data folder, installs it from
that release's wheel when it is not (the first session, and the first after a plugin update), and
tells the agent where it is. It needs Python 3.10+ (`python3`, `python` or `py -3`) and, on
Windows, Git Bash, which Claude Code runs hooks in. It does not touch any other Python install.
When it cannot install, the agent says so and gives the command to run by hand:

```
pip install "fireflymcf[full] @ git+https://github.com/dbergl/fireflymcf.git"
```

Describe the change you want ("rename the Water Pump button to Pump"), or use
`/fireflymcf:inspect`, `/fireflymcf:coach-profile`, `/fireflymcf:build` and
`/fireflymcf:flash-report`. The agent builds from your own stock `mcf.bin`, checks the whole image
against it, and tells you what has and has not been tested on a coach.

## Development

```
python3 -m venv .venv && .venv/bin/pip install -e '.[full,test]'
.venv/bin/python -m pytest -q -n auto          # bare checkout: only the corpus-free tests
scripts/link-research.sh [RESEARCH_REPO]       # link the private research data (default ../firefly-firmware)
.venv/bin/python -m pytest -q -n auto          # the full suite
```

Most tests read the firmware corpus, coach captures and render configurations, which are not
distributable and live in a private research repository. `scripts/link-research.sh` symlinks them
in under gitignored names; without them, the modules that need them are not collected and the run
says so. The tests that pin the reference coach's builds (its recipes and committed images) live
in that research repository, not here. The first full run is slow (recipe compiles fill
`build/.compiled-images`); later runs reuse it.

**Byte identity.** `scripts/golden.py record TABLE.json STOCK_DIR PROJECT.ffproj.json...` writes
the sha256 of each project's compiled image (its stock is found under `STOCK_DIR` by
`base_sha256`); `scripts/golden.py check` with the same arguments exits 1 and prints every project
whose image changed. Run both around a refactor that must not change any build.

## Releasing

Releases are GitHub releases; nothing is published to PyPI. A version tag starts
`.github/workflows/release.yml`: the test matrix on Linux, Windows and macOS, a check that the tag
matches both version files (`scripts/check_version.py`), then a release with generated notes and
the wheel and source package attached.

1. **Pick the version.** The last number for fixes only (0.2.0 → 0.2.1); the middle one for new
   features such as an op or a command (→ 0.3.0).
2. **Set it in both files, on a branch**: `version` in `pyproject.toml` and `"version"` in
   `plugin/.claude-plugin/plugin.json`. Check them:
   ```
   .venv/bin/python scripts/check_version.py v0.3.0    # prints "version 0.3.0", or what disagrees
   ```
3. **Merge to `main`, push, and wait for that push's CI run to pass.**
4. **Tag the commit on `main` and push the tag.** It must start with `v`; `0.3.0` starts nothing.
   ```
   git checkout main && git pull
   git tag -a v0.3.0 -m "fireflymcf 0.3.0"
   git push origin v0.3.0
   ```
5. **Watch the release run** (`gh run list`, or the Actions tab). It ends with the release at
   `https://github.com/dbergl/fireflymcf/releases/tag/v0.3.0`.

**If the run fails, nothing is published.** Fix it on `main`, then move the tag to the fixed
commit:

```
git push origin :refs/tags/v0.3.0
git tag -d v0.3.0
git tag -a v0.3.0 -m "fireflymcf 0.3.0"
git push origin v0.3.0
```

Move only a tag that never produced a release. Once a release exists, someone may have installed
it by its tag: ship the fix as the next version instead.

**The plugin installs the release its `plugin.json` names**, from that release's wheel. Between
the version bump reaching `main` and the release run publishing the wheel, a session that has
already picked up the new plugin finds no wheel; its hook says it did not install, writes no
marker, and the next session start installs it.

An owner installs a release with
`pip install "fireflymcf[full] @ git+https://github.com/dbergl/fireflymcf.git@v0.3.0"`.

## Disclaimer

**Independent and unofficial.** This project is not affiliated with, endorsed by, or supported by
Firefly Integrations, Jayco, or any coach or component manufacturer. Product names are the
trademarks of their owners and are used only to say what the tools work with.

**Interoperability, from files owners already have.** The tools and documentation come from
examining the configuration files and communications of equipment the author owns, so that owners
can customise and repair systems they own. No Firefly firmware, code or artwork is distributed
here: you supply your own coach's files, and every build is made from them on your computer.

**You are changing equipment that controls real loads.** A panel build can switch lights, pumps,
heaters, slides, awnings, inverters and battery systems. A wrong build can switch the wrong load,
stop a control working, or misreport a tank or battery. Flash only a build made from your own
coach's stock file, with the coach parked and in a safe state, check every change before relying
on it, and keep the stock `mcf.bin` on the USB stick so you can always go back. Modifying the
firmware may affect your warranty or support from the manufacturer or dealer.

**No warranty.** The software is provided "as is", without warranty of any kind, as the
[license](LICENSE) says. You use it, and anything you build or flash with it, at your own risk.

## License

[MIT](LICENSE)
