---
description: Write up what happened when the owner flashed a build, so unverified ops can become verified
argument-hint: "[path/to/mcf-NAME.bin]"
---

The owner has flashed a build (`$ARGUMENTS`, else the newest `build/mcf-*.bin` and its
`.bin.json` sidecar) and is reporting back. Fill in the report below from the sidecar, the
project file and **the owner's own observations**. Ask for anything missing; never fill an
observation in yourself. One row per op in the project, the unverified ones first.

```markdown
## Flash report: <build name>

- Build: `<file>`, sha256 `<sha256>`
- Stock: sha256 `<stock_sha256>`, build name `<stock build name>`, firmware `<section names>`
- Coach: <year, make, model>, floorplan <code>, options <list>
- Flashed: <date>. Booted: yes/no. Every unit updated: yes/no

| op id | type | verified before | what the owner saw | works? |
|---|---|---|---|---|
| ... | ... | yes / NO | ... | yes / no / not checked |

Anything else that changed, broke or looked wrong:

Photos: <attached / none>
Recovery needed: no / yes — <what happened>
```

Show the owner the finished report. It is meant for a GitHub issue on the fireflymcf repository
(template "Flash report"). Do not post it yourself: give them the text and let them decide where
it goes. The report must not include their coach's serial number, location or network details.
