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

The plugin drives the `fireflymcf` command-line tool, so install that first:

```
pip install "fireflymcf[full] @ git+https://github.com/dbergl/fireflymcf.git"
```

Then, at the prompt of a Claude Code session:

```
/plugin marketplace add dbergl/fireflymcf
/plugin install fireflymcf@fireflymcf
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

Most tests read the firmware corpus, coach captures and recipes, which are not distributable and
live in a private research repository. `scripts/link-research.sh` symlinks them in under
gitignored names; without them, the modules that need them are not collected and the run says so.
The first full run is slow (recipe compiles fill `build/.compiled-images`); later runs reuse it.

## Releasing

Releases are GitHub releases (nothing is published to PyPI). Set the same version in
`pyproject.toml` and `plugin/.claude-plugin/plugin.json`, commit, then tag and push the tag:

```
git tag v0.2.0 && git push origin v0.2.0
```

`.github/workflows/release.yml` runs the test matrix and checks the tag against both files
(`scripts/check_version.py`). It then attaches the wheel and the source package to a release
with generated notes. An owner installs a release with
`pip install "fireflymcf[full] @ git+https://github.com/dbergl/fireflymcf.git@v0.2.0"`.

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
