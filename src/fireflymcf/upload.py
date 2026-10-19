"""The RV-C **network upload** path — how a `G12` section reaches the device.

`LCD Firmware` and the `GuiCode`+`GuiData` pair are written by the panel into its own flash and
are invisible on the bus (`BUS-BASELINE.md` PC.37). The **`G12` row is not**: the G12 is a
separate node, and the loader has to move `0x25A8` bytes into `0x08038000` over CAN. This module
decodes that exchange from a `candump -L` capture.

**`observed-on-hardware`, 2026-09-08** (`BUS-BASELINE.md` PC.63,
`captures/pc63-g12-network-upload-2026-09-08.log`). The reconstruction below reproduces the G12
section of the flashed image **byte for byte**, which is the strongest possible check that the
decode is right: nothing here is fitted to the file, and the payload was never consulted while
deriving the framing.

The shape, all four parts confirmed against `MCF-FORMAT.md` §4.4's code derivation:

* The panel enters loader mode as source address **`0xF9`**, not its normal `0x9F`.
* The channel is **PROPRIETARY_A at priority 4** — `10EF<dst><src>`. Loader to G12 is
  `10EF9CF9`, G12 to loader `10EFF99C`. Priority is id bits 28-26, so `0x10……` is 4 against the
  coach's ordinary 6; that head start is what lets a 1.8 s transfer hold ~69% of a 250 kbit/s bus
  without the rest of the coach's 1 Hz traffic breaking it up. The decode never reads the
  priority -- it matches `PF == 0xEF` and the source address -- so this is documentation only.
* **The device pulls and the loader serves.** Every G12 frame is
  ``C1 FF <offset u24 LE> FF FF FF`` — a request for the 8 bytes at that offset — and the loader
  answers with exactly those 8 bytes. `MCF-FORMAT.md` §4.4 derived this from the
  receive-then-transmit shape at `cli.bin` `0x20015aae`/`0x20015b68`; the capture confirms it.
* **The two opcodes come from the MCF's own `G12` descriptor**, not from the device and not from
  the loader's code: tagged entry `0x41` `ConfigCmd` = `0xC0` and `0x42` `DataReqCmd` = `0xC1`,
  identical in all three builds (`MCF-FORMAT.md` §2). The loader reads them at `0x2001565a`,
  defaulting to `0xFF` when a descriptor does not carry them. A third slot, `0x43` `ConfigCmd2`,
  is **absent from every MCF in the corpus**, so the branch at `0x20015934` that would send it has
  never run here.
* **Each 32-bit word is sent byte-reversed.** This is the `strb` branch pair at `0x20015b46`
  versus `0x20015bba`, and it is the same byte-order trap `docs/G12-CODE.md` records for the
  `0x67` frame table. Reconstruct with `_unswap`, never by concatenating payloads.

Two frames are still `unknown`, and are reported rather than guessed at — see `Upload.control`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

#: Loader source address while the Spyder Config Loader is running.
LOADER_SA = 0xF9

#: `candump -L` line: `(<epoch>) <iface> <id>#<data>`.
_LINE = re.compile(r"\((\d+\.\d+)\)\s+\S+\s+([0-9A-Fa-f]{3,8})#([0-9A-Fa-f]*)")

#: The G12's per-chunk request opcode.
REQUEST = 0xC1

#: Bytes carried by one data frame.
CHUNK = 8

#: A free source address on the owner's bus, and the probe address of PC.21 and PC.57. The
#: enumeration request is sent from here rather than from `LOADER_SA`, so nothing we transmit
#: impersonates the panel's loader.
PROBE_SA = 0xC0

#: The PGN the loader asks for to start enumeration.
REQUEST_PGN = 0x1FED8

#: GENERIC_UPLOAD_STATUS -- one per upload target. **A RESPONSE, not a broadcast on the device's
#: own initiative** (corrected 2026-09-08, `BUS-BASELINE.md` PC.64): the panel broadcasts a
#: `1FED8` request every 14.0 s during normal operation and the G12 answers within ~6 ms. A
#: listener enumerates passively only because that poller is present.
STATUS_DGN = 0x0FF01

#: One parameter of one upload target. Sent **only** in answer to a request.
PARAMETER_DGN = 0x0FF02

#: `0FF02` ParamID -> the name Firefly's own loader log prints for it. Taken from
#: `captures/pc63-loader-logfile-2026-09-08.txt`, not invented here. The authoritative table
#: is the loader's own: `subcontainer.loader_param_names(cli)` reads all 0x83 slots straight
#: from cli.bin's region-2 sub-container (the array the loader indexes at `0x2001485A`), and
#: `test_container.py` pins this dict as a consistent subset of it.
PARAM_NAMES = {
    0x01: "FuncBank",
    0x02: "FuncInst",
    0x04: "ConfigRev",
    0x07: "ConfigType",
    0x08: "ConfigID",
    0x09: "ExtFwVer",
    0x0A: "FwRlsLetter",
    0x0B: "ExtFuncCode",
    0x0C: "HwVer/LcdDescType",
    0x0D: "HwOpt",
    0x0E: "BtldrVer",
    0x40: "LdAddr",
    0x80: "McuDevID",
    0x81: "McuFlashSize",
    0x82: "McuRamSize",
}


def dgn(can_id: int) -> int:
    """The 18-bit DGN of a frame.

    PDU1 (`PF < 240`) carries the destination address in the byte the DGN would otherwise use,
    so it is masked off -- `18EAFFF9` and `14EA9CF9` are both `0EA00`, one REQUEST.
    """
    pgn = (can_id >> 8) & 0x3FFFF
    return pgn if ((can_id >> 16) & 0xFF) >= 240 else pgn & 0x3FF00


def request_frame(target: int | None = None, source: int = PROBE_SA):
    """The REQUEST frame that starts enumeration: `(can_id, data)`.

    Broadcast when `target` is None, directed at one device otherwise. **Send the directed
    form**: PC.63's broadcast request drew the four `0FF01` status frames alone, while the
    directed one drew those *and* all 29 `0FF02` parameter frames.

    The two priorities -- 6 broadcast, 5 directed -- are not a choice. They are the ones the
    panel's own loader put on the wire, and `tests/test_upload.py` pins this against its frames.
    """
    payload = REQUEST_PGN.to_bytes(3, "little") + b"\xff" * 5
    if target is None:
        return (0x18EAFF00 | source, payload)
    return (0x14EA0000 | (target << 8) | source, payload)


@dataclass(frozen=True)
class UploadStatus:
    """A `0FF01`. One upload target the device advertises."""

    device_instance: int
    upload_type: int
    """1 = the application at `LdAddr 0x08006000`; 2 = the config an MCF's `G12` section is."""

    func_bank: int
    version: int
    """`[4:6]`, **big-endian** -- the one field in this protocol that is."""

    release_letter: int
    parameter_count: int
    """`[7]`. How many `0FF02` frames this target will send."""


@dataclass(frozen=True)
class UploadParameter:
    """A `0FF02`. One named value of one upload target."""

    device_instance: int
    upload_type: int
    param_id: int
    value: int
    """`[4:8]`, little-endian."""

    @property
    def name(self) -> str:
        return PARAM_NAMES.get(self.param_id, f"unknown-{self.param_id:#04x}")


#: `DGN_REQUEST`'s target, and a DGN the device also **broadcasts unprompted**: one frame carrying
#: the whole config identity that `0FF02` otherwise delivers a parameter at a time.
IDENTITY_DGN = 0x1FED8

#: RV-C NAME manufacturer code 293. Carried in bits 0-10 of `1FED8` payload `[0:2]`, and
#: independently in bits 21-31 of the NAME of every Firefly device that claims an address.
MANUFACTURER_FIREFLY = 293


@dataclass(frozen=True)
class ConfigIdentity:
    """A `1FED8` payload: one device's config identity in eight bytes.

    Decoded 2026-09-08 by reading the loader's own accessor `FUN_20014634` -- a `tbb` switch whose
    nine arms each read one field of this record out of the node cache -- and confirmed field for
    field against `captures/pc63-loader-logfile-2026-09-08.txt`, which prints five of them by name.

    The loader caches this frame at **slot 0** of its per-source-address node table
    (`0x20044554 + SA*0x2c`, written by `FUN_20014ef4`, dispatched at `0x2001789a`), and reads
    `manufacturer` back out of it to build the opening frame of a config upload.
    """

    manufacturer: int
    """`[0:2]` bits 0-10 -- the RV-C NAME manufacturer code. 293 for Firefly."""

    func_instance: int
    """`[0:2]` bits 11-15, equivalently `[1] >> 3`. The loader logs this as `FuncInst`."""

    func_bank: int
    """`[2]`. Logged as `FuncBank`."""

    flags: int
    """`[3]`. Read as two nibbles by `0x20014316`; **meaning undecoded**."""

    config_rev: int
    """`[4]`. Logged as `ConfigRev`."""

    config_id: int
    """`[5]`. Logged as `ConfigID`. One accessor arm reads bit 5 of it alone."""

    config_type: int
    """`[6:8]`. Logged as `ConfigType`."""


def parse_identity(frame: Frame):
    """`ConfigIdentity` if this frame is a `1FED8`, else None."""
    if dgn(frame.can_id) != IDENTITY_DGN or len(frame.data) < 8:
        return None
    d = frame.data
    packed = int.from_bytes(d[0:2], "little")
    return ConfigIdentity(
        manufacturer=packed & 0x7FF,
        func_instance=packed >> 11,
        func_bank=d[2],
        flags=d[3],
        config_rev=d[4],
        config_id=d[5],
        config_type=int.from_bytes(d[6:8], "little"),
    )


def opening_frame_payload(identity: ConfigIdentity) -> bytes:
    """The eight bytes the loader sends first, ahead of `ConfigCmd`.

    **Not an opaque constant.** `[1:3]` is the target's own manufacturer code, read back out of
    the `1FED8` it broadcast (`0x2001557c` `ubfx r3,r3,#0,#0xb`); the rest are `movs` literals
    (`0x200155b8`-`0x200155d8`) and a trailing 1 (`0x200155a2`). Observed on the wire as
    `00 25 01 AD 03 69 CF 01`, which this reproduces for a manufacturer of 293.
    """
    return bytes([0x00]) + (identity.manufacturer & 0x7FF).to_bytes(2, "little") + bytes(
        [0xAD, 0x03, 0x69, 0xCF, 0x01]
    )


def parse_status(frame: Frame):
    """`UploadStatus` if this frame is a `0FF01`, else None."""
    if dgn(frame.can_id) != STATUS_DGN or len(frame.data) < 8:
        return None
    d = frame.data
    return UploadStatus(
        device_instance=d[0],
        upload_type=d[1],
        func_bank=d[2],
        version=int.from_bytes(d[4:6], "big"),
        release_letter=d[6],
        parameter_count=d[7],
    )


def parse_parameter(frame: Frame):
    """`UploadParameter` if this frame is a `0FF02`, else None."""
    if dgn(frame.can_id) != PARAMETER_DGN or len(frame.data) < 8:
        return None
    d = frame.data
    return UploadParameter(
        device_instance=d[0],
        upload_type=d[1],
        param_id=d[2],
        value=int.from_bytes(d[4:8], "little"),
    )


class UploadError(Exception):
    """The capture does not contain a decodable network upload."""


@dataclass(frozen=True)
class Frame:
    time: float
    can_id: int
    data: bytes

    @property
    def source(self) -> int:
        return self.can_id & 0xFF

    @property
    def destination(self) -> int:
        return (self.can_id >> 8) & 0xFF


@dataclass
class Upload:
    """One decoded network config load."""

    device: int
    """The G12's source address (`0x9C` on this coach)."""

    loader: int
    """The loader's source address."""

    payload: bytes
    """The reconstructed section, word-order corrected. Gaps read as zero."""

    received: set = field(repr=False, default_factory=set)
    """Offsets whose 8 bytes actually arrived. A dropped frame leaves a hole here."""

    control: list = field(default_factory=list)
    """Loader frames that are not chunk data: `(time, bytes)`.

    Two occur in PC.63, both before the first request. `C0 00 00 00 A4 25 B0 CE` is the
    `ConfigCmd` frame built at `0x20015766`: `[0]` the opcode from descriptor tag `0x41`, `[1]`
    and `[4:6]` the length `0x25A4` (the section less its 4-byte stored checksum) split
    `>>16 / low / >>8`, and `[6:8]` a **byte sum of the payload mod 2^16, little-endian**: the
    loop at `0x20015880` reads one byte at a time and accumulates it inline at
    `0x200158b4`-`0x200158bc` (`ldrh`/`add`/`uxth`/`strh`). A second mode -- the STM32 CRC unit
    via `0x20000e2c` -- is selected by `cbz r7` at `0x20015894`, is NOT the path this frame took,
    and what sets `r7` is unknown. The opening frame
    `00 25 01 AD 03 69 CF 01` is built at `0x200155c0`: `[0]` zero, `[1:3]` an 11-bit value read
    from a `0x2c`-stride table (`ubfx #0,#0xb`), and `[3:8]` the fixed literals `AD 03 69 CF 01`
    emitted by `movs`. What that 11-bit field selects is **not decoded**.
    """

    ambiguous: bool = False
    """The capture holds a different number of requests and data frames.

    Set when a frame was lost on only one side of the exchange, which shifts FIFO pairing and
    makes every chunk after the loss suspect. `payload` is still returned -- refusing would throw
    away the part before the loss -- but nothing downstream should trust it while this is set.
    """

    start: float = 0.0
    end: float = 0.0

    @property
    def complete(self) -> bool:
        """Did every offset in the declared span arrive?"""
        return len(self.received) * CHUNK == len(self.payload)

    @property
    def missing(self) -> list:
        """Offsets the capture did not carry, in order."""
        return [o for o in range(0, len(self.payload), CHUNK) if o not in self.received]

    def matches(self, section: bytes) -> bool:
        """Does the transfer agree with `section` everywhere the capture actually saw?

        Gaps are skipped rather than counted as mismatches: a frame `candump` dropped under load
        is a property of the capture, not of the upload.
        """
        if len(section) != len(self.payload):
            return False
        return all(
            section[o : o + CHUNK] == self.payload[o : o + CHUNK] for o in self.received
        )


def _unswap(chunk: bytes) -> bytes:
    """Undo the loader's per-32-bit-word byte reversal."""
    return b"".join(chunk[i : i + 4][::-1] for i in range(0, len(chunk), 4))


def parse_frames(text: str):
    """Every CAN frame in a `candump -L` capture, in order."""
    out = []
    for line in text.splitlines():
        m = _LINE.match(line)
        if m:
            out.append(
                Frame(float(m.group(1)), int(m.group(2), 16), bytes.fromhex(m.group(3)))
            )
    return out


def find_uploads(frames, loader_sa: int = LOADER_SA):
    """Every network config load in a capture.

    A frame belongs to an upload if it is PROPRIETARY_A (`PF == 0xEF`) between the loader address
    and one device. The device is not assumed to be the G12: the source address is read off the
    frames, so a capture of some other node's upload decodes the same way.
    """
    pairs = {}
    for f in frames:
        if (f.can_id >> 16) & 0xFF != 0xEF:
            continue
        if f.source == loader_sa:
            pairs.setdefault(f.destination, []).append(f)
        elif f.destination == loader_sa:
            pairs.setdefault(f.source, []).append(f)

    uploads = []
    for device, group in sorted(pairs.items()):
        group.sort(key=lambda f: f.time)
        requests = [
            int.from_bytes(f.data[2:5], "little")
            for f in group
            if f.source == device and f.data[:1] == bytes([REQUEST])
        ]
        if not requests:
            continue
        # Control frames are the loader's opening exchange, before the first request. Everything
        # the loader sends after that is chunk data.
        opened = next(
            f.time for f in group if f.source == device and f.data[:1] == bytes([REQUEST])
        )
        sent = [f for f in group if f.source == loader_sa]
        control = [(f.time, f.data) for f in sent if f.time < opened]
        data = [f.data for f in sent if f.time >= opened]

        # Pair FIFO, not by strict alternation. The protocol is request-then-respond on the wire,
        # but a `candump` log can show a data frame ahead of its own request -- PC.63 does, once.
        # Strict alternation drops that chunk and reports the request as a stray control frame;
        # FIFO recovers it. When the two counts disagree the pairing is not trustworthy, so it is
        # flagged rather than guessed at.
        ambiguous = len(data) != len(requests)
        chunks = dict(zip(requests, data))

        size = max(requests) + CHUNK
        buf = bytearray(size)
        for off, raw in chunks.items():
            buf[off : off + CHUNK] = _unswap(raw)
        uploads.append(
            Upload(
                device=device,
                loader=loader_sa,
                payload=bytes(buf),
                received=set(chunks),
                control=control,
                ambiguous=ambiguous,
                start=group[0].time,
                end=group[-1].time,
            )
        )
    return uploads


def decode(text: str, loader_sa: int = LOADER_SA) -> Upload:
    """The single upload in a capture. Raises if there is not exactly one."""
    uploads = find_uploads(parse_frames(text), loader_sa)
    if not uploads:
        raise UploadError(
            f"no PROPRIETARY_A exchange with source address {loader_sa:#04x} in this capture -- "
            "the loader ran but no Network Config Load was performed, or the capture missed it"
        )
    if len(uploads) > 1:
        raise UploadError(
            f"{len(uploads)} devices were uploaded to; use find_uploads() and pick one"
        )
    return uploads[0]
