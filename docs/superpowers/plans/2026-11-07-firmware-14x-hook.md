# Firmware 14.x, Plan 2: The Per-Tick Hook — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** fireflymcf finds the per-tick hook, its stock callee, the stock append point and the host-API cells in any image, instead of taking 13.6's addresses, so authored GuiCode routines (user tick ops first) run on 14.7.

**Architecture:**
- A new module, `hooklocate`, reads the hook from structure. The hook is the first `bl` of `GuiCode_entry1` (GuiCode header word `+0x10`). The walk follows appended links to the stock callee, and proves that callee by three checks: its prologue, its `+0x214` config-buffer load, and that its only `bl` caller is the hook.
- The host-API cells come from `GuiCode_entry0`'s stores (header `+0x0C`).
- `followchannel`'s 13.6 constants become calls to the locator. Every user of them reads the located value.

On 13.6 the locator returns today's numbers, so 13.6 builds stay byte-identical.

**Tech Stack:** Python 3.11+, capstone (already a dependency), pytest, `fireflymcf.emu`.

**Spec:** `docs/superpowers/specs/2026-11-06-firmware-14x-support-design.md` (pin 3; goals 1-3)

**Research:** 2026-11-07. The prototype (`locate_hook.py`) returned 13.6's pins exactly on stock. On 45 built 13.6 images it reproduced `chain_from_hook`'s chain 45 of 45. The findings below are the evidence this plan cites.

| | 13.6 | 14.7 | 11.23 |
|---|---|---|---|
| entry1 (hdr +0x10) | `0x24EE28` | `0x24F274` | `0x24CE10` |
| hook (offset, bytes) | `0xEE3C`, `f8f7dcfe` | `0xF288`, `f8f710fd` | `0xCE24`, `faf7b4fb` |
| stock callee F | `0x247BF8` (`0x247C18 ldr.w r3,[r4,#0x214]`) | `0x247CAC` (`0x247CCC …#0x214`) | `0x247590` (`0x247598 ldr.w r3,[r0,#0x214]`) |
| stock append point | `0x259BB4` | `0x25C4CC` | `0x257548` |
| host +0x04 (variant setter) | `0x20039908` | `0x200395FC` | `0x20039304` |
| host +0x08 (frame sender) | `0x2003986C` | `0x20039558` | `0x20039294` |

The stock append point is `0x00240000 + len(stock GuiCode) - 4`. It cannot be recovered from a built image alone. The section carries no length and nothing references its end; this is a defensible negative.

## Global Constraints

- **13.6 stays byte-identical.** Both suites must pass: this repo's, and the research repo's byte pins (`cd ../firefly-firmware && .venv/bin/python -m pytest tests -q -n auto`).
- **No 14.7 constant becomes a locator.** The 14.7 and 11.23 values above appear only as test expectations.
- **The locator refuses; it never guesses.** It refuses on a missing Thumb vector, a branch or an `r0` write before the first `bl`, a link without its epilogue, a loop, a callee without the `+0x214` load, a callee with another `bl` caller, or entry0 storing one offset twice. Each refusal is a `FollowChannelError` subclass carrying the message.
- **Located values are memoised through `memo`**, keyed on the image bytes (CLAUDE.md trap).
- **Docs cite evidence by date, never by build name.** `scripts/scrub_check.py` must exit 0.
- **No `"14.7"` joins an op's `firmwares` in this plan.** The built-in live-status ops still need the cell-table and donor locators (Plans 3 and 4). This plan enables user tick ops, which are exempt from the lists.

## Review Focus

1. **A built image used as stock.** Its hook already points at a link. `stock_append_point` must refuse it ("stock has links"), not return a wrong end (Task 1 `test_append_point_refuses_a_built_image`).
2. **A user op that rewrote the hook site with non-`bl` bytes.** `locate_hook` refuses. `_check_tick_chain` keeps returning silently on a non-`TickChainFull` error, as v0.6.1 does (Task 2 keeps `test_tick_chain_limit.py` green).
3. **The chain lower bound.** Each link must sit at or above the stock append point when the stock is known. A link pointing into stock code is refused (Task 1 `test_link_below_append_point_refused`).
4. **14.7's host-API meaning.** `+0x08` really sends, as proved by an emulator frame (Task 4).
5. **Cache poisoning.** A test that monkeypatches an image must not leak a located value into another test (memo on bytes; Task 1 `test_memo_keys_on_bytes`).

---

### Task 1: `hooklocate` — locate the hook, the chain, the stock callee, the append point, the host cells

**Files:**
- Create: `src/fireflymcf/hooklocate.py`
- Test: `tests/test_hooklocate.py`

**Interfaces (produces):**
- `@dataclass(frozen=True) class Hook: entry1: int; hook_addr: int; hook_off: int; hook_bytes: bytes; links: tuple[int, ...]; stock_callee: int; proof: tuple[str, ...]`
- `locate_hook(data: bytes, *, stock: bytes | None = None, limit: int = 64) -> Hook`. When `stock` is given, every link must lie in `[stock_append_point(stock), section end)`.
- `stock_append_point(stock: bytes) -> int`. Refuses unless `locate_hook(stock).links == ()`.
- `host_api_cells(data: bytes) -> dict[int, int]`: `{api offset: SRAM cell}`.
- `class HookNotFound(followchannel.FollowChannelError)`

- [ ] **Step 1: Write the failing tests** (`NEEDS_RESEARCH_DATA`; fixtures `mcf_136`, `mcf_147`, `mcf_1123`):
  - `test_stock_values`: assert the table above for all three images: `hook_off`, `hook_bytes`, `stock_callee`, `stock_append_point`, `host_api_cells()[4]`, `host_api_cells()[8]`, with `links == ()`.
  - `test_matches_chain_from_hook_on_built_images`: for every `builds/*.bin` (symlinked research builds) whose stock is 13.6, assert `list(h.links) + [h.stock_callee] == followchannel.chain_from_hook(img)`.
  - `test_append_point_refuses_a_built_image`.
  - `test_link_below_append_point_refused`: point the hook `bl` at a stock-code `push {r0-r3,lr}` region.
  - `test_link_without_tail_refused`.
  - `test_callee_with_two_callers_refused`: add a second `bl F` in a scratch area.
  - `test_entry1_branch_before_bl_refused`.
  - `test_memo_keys_on_bytes`.
- [ ] **Step 2: Run them:** `.venv/bin/python -m pytest tests/test_hooklocate.py -q`. Expected: ImportError.
- [ ] **Step 3: Implement.** Use the method in the research table's header row: the entry1 vector, the first `bl` within 0x40 bytes, the link walk (`push {r0, r1, r2, r3, lr}` … `pop.w {r0, r1, r2, r3, lr}; b.w`), and the callee proof. The callee proof's `bl`-caller check is a scan of every `bl` encoding in the section. Each proof line names the address and the instruction. Disassemble with capstone, as `thumb.py`/`slottrace.py` do; reuse `slottrace.guicode_entry1` if it fits.
- [ ] **Step 4: Run the tests.** All pass. Then stub the callee's caller scan to return `[]` and confirm `test_stock_values` fails (evidence rule: check your tests can fail). Revert the stub.
- [ ] **Step 5: Commit:** `"hooklocate: find the per-tick hook, its chain, callee, append point and host cells"`

### Task 2: The pins become located values

**Files:**
- Modify: `src/fireflymcf/followchannel.py`. `TAIL_TARGET`, `HOOK_OFF`, `HOOK_STOCK`, `STOCK_APPEND_POINT`, `SENDER_PTR_ADDR` and `VARIANT_SETTER` (lines 45-64) are no longer used as locators. `hook_state`, `chain_from_hook` and `hook_bytes` call `hooklocate`. Keep the names as deprecated module-level 13.6 values only if a test or the display bridge still needs them. The display bridge (lines ~1008-1087) stays 13.6-only and must refuse other firmware.
- Modify: `recipe_ops.py`.
  - `_check_append_point` (~5560) and `APPEND_POINT` (~7111) take `stock_append_point(ctx.stock)`.
  - The verify asserts that compare against `HOOK_STOCK`/`TAIL_TARGET` (~5543-5551, 5850-5858, 6274, 7220-7227, 8501, 8811, 9488, 10579-10584, 11817) compare against the located `Hook`.
- Modify: `routinekit.py` (`_plan` ~109-113, `TickPlan`), `readout.py:288`, `bitcombine.py:92`, `statusfold.py:191`, `pumpauto.py:358`, `recipe_ops.py:2688`: these read `hook_state` and the hook offset from the locator. Where a function sees only `data`, add a keyword `stock: bytes | None = None` and pass `ctx.stock` from op code.
- Modify: `recipe.py:595-598` (`_check_tick_chain`): uses `hooklocate`. On any other `FollowChannelError` it returns silently, as today.
- Test: the existing suites, unchanged.

- [ ] **Step 1:** Grep `src/` for every name listed above. Write the list into the commit message body.
- [ ] **Step 2:** Replace each use.
- [ ] **Step 3: Run** the full suite and the research byte pins. Expected: all pass, with no test changed except imports of removed names.
- [ ] **Step 4: Commit:** `"followchannel, routinekit, ops: the hook, chain and append point are located"`

### Task 3: Sender and setter cells come from the image

**Files:**
- Modify: `routinekit.py:508`, `userop.py:350`, `pumpauto.py:369` (sender) and `statusfold.py:94` (setter). Each uses `host_api_cells(data)[0x08]` or `[0x04]`.
- Test: `tests/test_hooklocate.py::test_routine_pools_use_located_sender`. Build a one-routine tick op on 14.7 with `routinekit.install_tick`, then assert its literal pool holds `host_api_cells(mcf_147)[8]` and not `0x2003986C`.

- [ ] **Steps:** write the test, then run it (FAIL: it finds the 13.6 cell), implement, run the full suite and the research byte pins, then commit: `"routinekit, userop: the frame sender and variant setter cells are located"`.

### Task 4: User tick ops run on 14.7, proved in the emulator

**Files:**
- Test: `tests/emu/test_hook147.py`
- Modify: `plugin/skills/write-new-op/USER-OPS.md`. User tick ops run on 13.6 and 14.7. The hook, chain limit and sender are found per image.

Tests, on `Scenario` over a 14.7 image built through `routinekit.install_tick`:
- `test_hook_fires_each_tick`: a link increments a word above `0x2000AD98`; two `read_mem` reads one second apart differ. The halfword the stock callee increments at its entry (the literal it loads at `0x247CB8`, a 14.7 test expectation) also advances, and HOME's screenshot equals stock's.
- `test_chain_of_two`: two links, both counters advance, and `chain_from_hook == [second, first, callee]`.
- `test_sender_cell_sends`: a latched link sends one fixed frame through `host_api_cells()[8]`. `frames()` shows exactly one, from the panel's source address. This settles the inference that `+0x08` is the sender on 14.7's LCD firmware.
- `test_user_op_tick_on_147`: the `tests/fixtures/userops` tick fixture (or a minimal one) compiles on 14.7 with `compile_project` and its effect is observable in the emulator.

- [ ] **Steps:** write the tests. Run them; expected FAIL before Tasks 1-3, or document why one passes. Fix any emulator gap in the emulator, not in the tests. Run `tests/emu`, the full suite and `scrub_check`. Commit: `"emu: authored tick routines run on 14.7"`.
- [ ] The setter cell (`+0x04`) needs the variant-byte location on 14.7, which belongs to the donors or cell-table work. Record it as an open item; do not test it here.

### Task 5: `decodeguards`' decoder range

**Files:**
- Modify: `src/fireflymcf/decodeguards.py:32-33`. `DECODER_START` = the located stock callee.
- `DECODER_END` (`0x24AB00`, the end of the callee's decode block) needs its own structural end. Read `decodeguards.py` and the 13.6 code at `0x24AB00` to find what marks the end.
- If the end is found from structure, unique on 13.6 and 14.7 and equal to `0x24AB00` on 13.6, implement it with a proof line.
- If not, `range_guards` refuses on images other than 13.6 with a clear error. Readouts on 14.7 then wait for it.
- Test: `tests/test_hooklocate.py::test_decoder_range`.

- [ ] **Steps:** test, then fail, implement or refuse, run both suites, commit: `"decodeguards: the decoder range comes from the located callee"`.

### Task 6: Hand-off

- [ ] Report: what user tick ops can now do on 14.7, and the setter cell and decoder-end status. Release through the tag workflow only with the owner's approval.
