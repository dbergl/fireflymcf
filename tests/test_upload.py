"""The network upload decode, held to the one oracle that cannot be fitted.

The capture is of the owner flashing `mcf-experiment-v22-notankbcast.bin`'s G12 section
(`BUS-BASELINE.md` PC.63). That image is reproducible from stock without any build artifact:
stock 13.6 + `tankbroadcast.suppress` + `reseal`. So the test rebuilds the section it expects and
compares it against bytes recovered from the wire, and neither side ever looks at the other.
"""

import re
from pathlib import Path

import pytest

from fireflymcf import container, patch, tankbroadcast, upload

#: `ConfigCmd`, MCF descriptor tagged entry 0x41. `upload.REQUEST` is its `DataReqCmd` twin.
CONFIG_CMD = 0xC0

ROOT = Path(__file__).resolve().parents[1]
CAPTURE = ROOT / "captures" / "pc63-g12-network-upload-2026-09-08.log"
LOADER_LOG = ROOT / "captures" / "pc63-loader-logfile-2026-09-08.txt"
STOCK = ROOT / "firefly-13.6" / "mcf.bin"


@pytest.fixture(scope="module")
def transfer():
    return upload.decode(CAPTURE.read_text())


def g12_section(data: bytes) -> bytes:
    section = next(
        s for s in container.parse_container(data).sections if s.name.startswith("G12")
    )
    return data[section.offset : section.offset + section.length]


@pytest.fixture(scope="module")
def flashed_section():
    """The G12 section of v22, rebuilt from stock rather than read from `build/`."""
    out, _ = tankbroadcast.suppress(STOCK.read_bytes())
    out, _ = patch.reseal(out)
    return g12_section(out)


def test_the_capture_holds_one_upload_from_the_loader_to_the_g12(transfer):
    assert transfer.loader == upload.LOADER_SA == 0xF9
    assert transfer.device == 0x9C


def test_the_transfer_reproduces_the_flashed_section_byte_for_byte(
    transfer, flashed_section
):
    """The oracle. 9,632 of 9,640 bytes recovered from CAN, every one of them correct."""
    assert len(transfer.payload) == len(flashed_section) == 0x25A8
    assert not transfer.ambiguous
    assert transfer.matches(flashed_section)
    assert len(transfer.received) * upload.CHUNK == 0x25A8 - upload.CHUNK


def test_it_is_NOT_the_stock_section_it_is_the_patched_one(transfer):
    """Ten bytes separate v22's G12 from stock's; the wire carries v22's."""
    stock = g12_section(STOCK.read_bytes())
    assert not transfer.matches(stock)
    differing = [
        i
        for i in range(len(stock))
        if i // upload.CHUNK * upload.CHUNK in transfer.received
        and stock[i] != transfer.payload[i]
    ]

    # four 2-byte `blx r3` -> nop edits plus the section checksum word
    assert len(differing) == 10


def test_the_per_word_byte_swap_is_load_bearing(transfer, flashed_section):
    """Skip `_unswap` and the payload stops matching -- so the swap is not decoration."""
    raw = b"".join(
        transfer.payload[o : o + 4][::-1] + transfer.payload[o + 4 : o + 8][::-1]
        for o in range(0, len(transfer.payload), upload.CHUNK)
    )
    assert raw != flashed_section


def test_the_one_dropped_chunk_is_reported_not_silently_zeroed(transfer):
    """`0x418` lost its request AND its data, so it is gone. `0x1B0` only lost frame ORDER."""
    assert not transfer.complete
    assert transfer.missing == [0x418]


def test_a_data_frame_logged_ahead_of_its_own_request_is_still_paired(transfer):
    """Strict alternation mislaid `0x1B0` and reported its data as a stray control frame.

    The log shows the chunk before the `C1FFB00100` that asked for it -- a `candump` ordering
    artifact, not a protocol event. FIFO pairing recovers it, and the recovered bytes are the
    section's own, which is the check that the recovery is right rather than merely plausible.
    """
    assert 0x1B0 in transfer.received
    assert transfer.payload[0x1B0:0x1B8] == upload._unswap(
        bytes.fromhex("04ffc8010200ffff")
    )


def test_the_control_frames_are_surfaced_rather_than_discarded(transfer):
    """Two, both before the first request, and only the second is fully decoded."""
    frames = [d.hex() for _, d in transfer.control]
    assert frames == ["002501ad0369cf01", "c0000000a425b0ce"]
    header = transfer.control[1][1]
    assert header[0] == CONFIG_CMD
    # [4:6] is the section length less its 4-byte stored checksum
    assert int.from_bytes(header[4:6], "little") == 0x25A8 - 4


def test_the_opcodes_come_from_the_mcf_descriptor_not_from_the_loader():
    """`C0`/`C1` are descriptor tagged entries `0x41`/`0x42`, the same on all three builds.

    And tag `0x43` (`ConfigCmd2`) is carried by none of them, so the loader branch that would
    send it has never run on this corpus.
    """
    for build in sorted(ROOT.glob("firefly-*/mcf.bin")):
        data = build.read_bytes()
        parsed = container.parse_container(data)
        offsets = container._descriptor_offsets(data, len(parsed.sections), 0xA4)
        g12 = next(
            off
            for section, off in zip(parsed.sections, offsets)
            if section.name.startswith("G12")
        )
        count = int.from_bytes(data[g12 + 0x46 : g12 + 0x48], "little")
        tags = {
            int.from_bytes(data[g12 + 0x58 + 8 * k : g12 + 0x58 + 8 * k + 2], "little"): int.from_bytes(
                data[g12 + 0x58 + 8 * k + 4 : g12 + 0x58 + 8 * k + 8], "little"
            )
            for k in range(count)
        }
        assert tags[0x41] == CONFIG_CMD == 0xC0, build
        assert tags[0x42] == upload.REQUEST == 0xC1, build
        assert 0x43 not in tags, build


def test_a_capture_with_no_upload_is_refused_with_a_useful_message():
    """Every other capture in the corpus has the loader run at most, never a transfer."""
    quiet = (ROOT / "captures" / "pc37-aes-trigger-attempt-2026-08-24.log").read_text()
    with pytest.raises(upload.UploadError, match="no PROPRIETARY_A exchange"):
        upload.decode(quiet)


def test_the_loader_log_and_the_bus_agree_on_the_flash(transfer):
    """The stick's own account of the same event -- two sources, neither derived from the other."""
    log = LOADER_LOG.read_text()
    assert "Start Network Config Load" in log
    assert "Upload Complete" in log and "Config Load Success" in log
    assert "Src Addr 9C, Num Gen Upld Status Rxd = 4" in log
    assert transfer.device == 0x9C


# --------------------------------------------------------------------------------------------
# The wire format in the OTHER direction: encoding the loader's REQUEST, and decoding the two
# enumeration frames the device answers with. Both halves are pinned by evidence we did not
# write -- the panel's own frames, and Firefly's own log.
# --------------------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def frames():
    return upload.parse_frames(CAPTURE.read_text())


@pytest.fixture(scope="module")
def logged_parameters():
    """`(DeviceInst, UploadType, name, value)` for all 29, from the loader's own log.

    The log is Firefly's account of the same exchange, written by their code on the panel. It
    names every parameter and prints every value, so it is an oracle for our decode that shares
    no derivation with it.
    """
    out = []
    for line in LOADER_LOG.read_text().splitlines():
        m = re.match(
            r"Rx gen upld param: DeviceInst = (\d+) UploadType = ([0-9A-F]+) "
            r"ID (\S+) = ([0-9A-F]+)",
            line.strip(),
        )
        if m:
            out.append((int(m.group(1)), int(m.group(2), 16), m.group(3), int(m.group(4), 16)))
    return out


def test_the_loader_log_names_all_twenty_nine_parameters(logged_parameters):
    """Guard on the oracle itself: a reworded log would silently weaken every test below."""
    assert len(logged_parameters) == 29


def test_request_frame_reproduces_the_loaders_own_broadcast(frames):
    sent = next(f for f in frames if f.can_id == 0x18EAFFF9)
    assert upload.request_frame(source=0xF9) == (sent.can_id, sent.data)


def test_request_frame_reproduces_the_loaders_own_directed_request(frames):
    """Directed is priority 5, broadcast priority 6. Not a choice -- what the panel sent."""
    sent = next(f for f in frames if f.can_id == 0x14EA9CF9)
    assert upload.request_frame(target=0x9C, source=0xF9) == (sent.can_id, sent.data)


def test_every_parameter_the_loader_logged_decodes_to_the_same_value(frames, logged_parameters):
    decoded = {
        (p.device_instance, p.upload_type, p.name): p.value
        for p in (upload.parse_parameter(f) for f in frames)
        if p is not None
    }
    assert len(decoded) == 29
    for instance, kind, name, value in logged_parameters:
        assert decoded[(instance, kind, name)] == value, name


def test_each_status_frame_declares_the_parameter_count_that_actually_arrives(frames):
    statuses = {
        (s.device_instance, s.upload_type): s
        for s in (upload.parse_status(f) for f in frames)
        if s is not None
    }
    order = [(0, 1), (0, 2), (0, 0xFF), (1, 1)]
    assert sorted(statuses, key=order.index) == order
    assert [statuses[k].parameter_count for k in order] == [10, 7, 6, 6]
    assert sum(s.parameter_count for s in statuses.values()) == 29


def test_the_status_frames_outlive_the_update_window_and_the_parameter_dump_does_not(frames):
    """A listener that transmits nothing still learns every upload target.

    `0FF01` arrives throughout the capture, not just while the loader is running; `0FF02` answers
    a request and nothing else. This is what licenses the probe's passive rung -- though see
    `test_the_status_frames_are_answers_to_the_panels_poll` for *whose* request the passive
    frames are answering, which is not the device's own initiative.
    """
    window = (1788885986.23, 1788886045.07)
    status = [f for f in frames if upload.dgn(f.can_id) == upload.STATUS_DGN]
    parameter = [f for f in frames if upload.dgn(f.can_id) == upload.PARAMETER_DGN]
    inside = lambda fs: [f for f in fs if window[0] <= f.time <= window[1]]

    assert len(status) == 67 and len(inside(status)) == 12
    assert len(parameter) == 29 and len(inside(parameter)) == 29


def test_the_directed_request_is_the_one_that_dumps_parameters(frames):
    """The broadcast drew four status frames; the directed request drew those AND all 29.

    This is why the probe's active rung sends the directed form.
    """
    broadcast = next(f for f in frames if f.can_id == 0x18EAFFF9).time
    directed = next(f for f in frames if f.can_id == 0x14EA9CF9).time
    after = lambda t, fs: [f for f in fs if t < f.time < t + 0.5]

    status = [f for f in frames if upload.dgn(f.can_id) == upload.STATUS_DGN]
    parameter = [f for f in frames if upload.dgn(f.can_id) == upload.PARAMETER_DGN]

    assert len(after(broadcast, status)) == 4
    assert after(broadcast, parameter) == []
    assert len(after(directed, status)) == 4
    assert len(after(directed, parameter)) == 29


def test_a_frame_that_is_neither_status_nor_parameter_decodes_to_none(frames):
    chunk = next(f for f in frames if f.can_id == 0x10EF9CF9)
    assert upload.parse_status(chunk) is None
    assert upload.parse_parameter(chunk) is None


def test_a_status_frames_version_agrees_with_the_same_targets_parameters(frames):
    """`0FF01[4:6]` is big-endian while every `0FF02` value is little-endian.

    Nothing pinned that until this test: flipping the status field's byte order left all 18
    other tests green. The cross-check is free -- a target that reports `ExtFwVer` and
    `FwRlsLetter` as parameters must agree with what its own status frame declared.
    """
    statuses = {
        (s.device_instance, s.upload_type): s
        for s in (upload.parse_status(f) for f in frames)
        if s is not None
    }
    parameters = {
        (p.device_instance, p.upload_type, p.name): p.value
        for p in (upload.parse_parameter(f) for f in frames)
        if p is not None
    }

    checked = 0
    for key, status in statuses.items():
        for name, declared in (("ExtFwVer", status.version),
                               ("FwRlsLetter", status.release_letter)):
            if (*key, name) in parameters:
                assert parameters[(*key, name)] == declared, (key, name)
                checked += 1
    assert checked == 4, "both targets that report a version must have been compared"


def test_the_declared_load_addresses_tile_the_declared_flash_exactly(frames):
    """The G12's whole flash map, read off the bus (PC.65).

    Three upload targets name a `LdAddr`, and `McuFlashSize` names the flash. The units of
    `McuFlashSize` are not stated anywhere -- but KB is the **only** reading on which all three
    load addresses land inside the part, and on that reading the three regions tile it with no
    gap and no overlap. That is the argument for the units, and for the map.
    """
    parameters = {
        (p.device_instance, p.upload_type, p.name): p.value
        for p in (upload.parse_parameter(f) for f in frames)
        if p is not None
    }
    base = 0x08000000
    flash_kb = parameters[(0, 1, "McuFlashSize")]
    assert flash_kb == 0x100
    assert parameters[(0, 0xFF, "McuFlashSize")] == flash_kb, "the device agrees with itself"

    bootloader = parameters[(1, 1, "LdAddr")]
    application = parameters[(0, 1, "LdAddr")]
    config = parameters[(0, 2, "LdAddr")]
    end = base + flash_kb * 1024

    assert [bootloader, application, config] == [0x08000000, 0x08006000, 0x08038000]
    assert bootloader == base, "the first target starts at flash base"
    assert base < application < config < end
    assert (application - bootloader, config - application, end - config) == (
        24 * 1024, 200 * 1024, 32 * 1024)

    # Bytes would put the whole part in 256 bytes; nothing else fits either.
    assert not all(base <= a < base + flash_kb for a in (application, config))


def test_the_g12_section_fits_the_config_region_with_room_to_spare(frames):
    """9,640 bytes into 32 KB. Growth is not what constrains a G12 edit."""
    parameters = {
        (p.device_instance, p.upload_type, p.name): p.value
        for p in (upload.parse_parameter(f) for f in frames)
        if p is not None
    }
    region = (0x08000000 + parameters[(0, 1, "McuFlashSize")] * 1024) - parameters[(0, 2, "LdAddr")]
    transfer = upload.decode(CAPTURE.read_text())
    assert len(transfer.payload) == 0x25A8
    assert len(transfer.payload) < region
    assert region == 32 * 1024


def test_the_status_frames_are_answers_to_the_panels_poll_not_a_device_broadcast(frames):
    """Corrects PC.64 Result 1, and the reason it was wrong is a standing shape here.

    `0FF01` arrives on a regular ~14 s beat, which reads as a device broadcasting on its own
    initiative -- and that is what PC.64 first claimed. It is not. **The panel broadcasts a
    `1FED8` request every 14.0 s** during normal operation, and the G12 answers within
    milliseconds. The regularity belongs to the poller, not the responder.

    So a passive listen works only while something else is asking. On a bus with no panel it
    hears nothing, and that silence would mean no poller rather than no device.

    *An observation of a system with one input describes the pair, not the consumer.*
    """
    import bisect

    polls = sorted(
        f.time
        for f in frames
        if upload.dgn(f.can_id) == 0x0EA00
        and f.data[:3] == upload.REQUEST_PGN.to_bytes(3, "little")
        and f.source == 0x9F
    )
    status = sorted(f.time for f in frames if upload.dgn(f.can_id) == upload.STATUS_DGN)

    assert len(polls) == 14
    intervals = [round(b - a, 1) for a, b in zip(polls, polls[1:])]
    assert set(intervals) == {14.0}, "the panel polls on a fixed 14.0 s beat"

    prompt = [t - polls[bisect.bisect_left(polls, t) - 1] for t in status
              if bisect.bisect_left(polls, t) > 0]
    assert len(prompt) == len(status) == 67
    # The 12 that are not the panel's are the loader's own two requests during the update.
    assert sum(1 for gap in prompt if gap < 0.1) == 55
    assert sorted(prompt)[27] < 0.01, "the median answer lands within 10 ms of the poll"


def test_the_config_command_check_word_is_a_byte_sum_of_the_payload(transfer, flashed_section):
    """Decoded 2026-09-08, and it is what makes writing possible at all.

    The `C0` header's last two bytes are a **byte sum mod 2^16, little-endian** -- not a CRC, and
    not the four-bytes-at-a-time accumulation this file's docstring used to claim. The loop at
    `0x20015880` reads one byte (`ldrb.w` after a length-1 read) and accumulates it inline at
    `0x200158b4`-`0x200158bc`: `ldrh` / `add` / `uxth` / `strh`.

    Held against the frame the panel actually sent, so this is the generator's oracle: without it
    a write cannot build a header the device will accept.
    """
    config = next(d for _, d in transfer.control if d[:1] == bytes([CONFIG_CMD]))
    assert config == bytes.fromhex("c0000000a425b0ce")

    declared = config[1] << 16 | config[5] << 8 | config[4]
    assert declared == len(flashed_section) - 4 == 0x25A4, "the section less its stored checksum"

    body = flashed_section[:declared]
    assert sum(body) & 0xFFFF == int.from_bytes(config[6:8], "little") == 0xCEB0


def test_the_device_never_sends_anything_but_a_chunk_request(transfer, frames):
    """Settles how the loader's outcome codes reach it: they do not come over the wire.

    The loader compares `0xC0`/`0xD0`/`0xF0` at `0x20015cac`/`0x20015cda`/`0x20015d08`, which
    reads like a device status. It is not: those run AFTER `"Upload Complete"` and switch on which
    command was run, then re-read a 13-bit field whose all-ones value is the unset sentinel. The
    device itself says nothing but `C1` -- every frame, for the whole transfer.
    """
    device = [f for f in frames if f.can_id == 0x10EFF99C]
    assert len(device) == 1204
    assert {f.data[0] for f in device} == {upload.REQUEST} == {0xC1}


def test_the_opening_control_frame_carries_the_fixed_literals_the_loader_emits(transfer):
    """Every byte of the opening frame is placed (spec §2.3); only `0x125`'s meaning is open."""
    opening = next(d for _, d in transfer.control if d[:1] == b"\x00")
    assert opening == bytes.fromhex("002501ad0369cf01")
    assert opening[0] == 0x00
    assert int.from_bytes(opening[1:3], "little") == 0x0125
    assert (int.from_bytes(opening[1:3], "little") >> 11) == 0, "an 11-bit field"
    assert opening[3:7] == bytes([0xAD, 0x03, 0x69, 0xCF]), "movs literals"
    assert opening[7] == 0x01


# --------------------------------------------------------------------------------------------
# `1FED8` -- the config identity the device broadcasts, and the source of the opening frame's
# one previously-undecoded field.
# --------------------------------------------------------------------------------------------


def test_the_device_broadcasts_its_whole_config_identity_in_one_frame(frames):
    """`1FED8` carries, in eight bytes, what `0FF02` delivers one parameter at a time."""
    identities = {
        f.source: upload.parse_identity(f)
        for f in frames
        if upload.parse_identity(f) is not None
    }
    assert set(identities) == {0x9C}
    g12 = identities[0x9C]
    assert g12.manufacturer == upload.MANUFACTURER_FIREFLY == 293
    assert g12.func_instance == 0x16
    assert g12.func_bank == 0x1F
    assert g12.config_rev == 6
    assert g12.config_id == 0x16
    assert g12.config_type == 0x1234


def test_the_identity_frame_agrees_with_the_loaders_own_log(frames, logged_parameters):
    """Same values, two sources: one 1FED8 broadcast, and Firefly's log of 29 0FF02 frames."""
    identity = next(
        i for i in (upload.parse_identity(f) for f in frames) if i is not None
    )
    logged = {name: value for _, kind, name, value in logged_parameters if kind == 2}
    assert logged["FuncBank"] == identity.func_bank
    assert logged["FuncInst"] == identity.func_instance
    assert logged["ConfigRev"] == identity.config_rev
    assert logged["ConfigID"] == identity.config_id
    assert logged["ConfigType"] == identity.config_type


def test_the_opening_frame_is_computed_from_the_identity_not_replayed(transfer, frames):
    """Retires the assumption that `0x125` had to be replayed verbatim.

    The loader reads the manufacturer code out of the device's own `1FED8` (cached at slot 0 of
    its node table) and puts it in `[1:3]`. Everything else is a `movs` literal. So the frame is
    reproducible for any device, not just this one.
    """
    identity = next(
        i for i in (upload.parse_identity(f) for f in frames) if i is not None
    )
    opening = next(d for _, d in transfer.control if d[:1] == b"\x00")
    assert upload.opening_frame_payload(identity) == opening


def test_a_frame_that_is_not_an_identity_decodes_to_none(frames):
    chunk = next(f for f in frames if f.can_id == 0x10EF9CF9)
    assert upload.parse_identity(chunk) is None


def test_the_manufacturer_field_is_eleven_bits_wide_not_twelve():
    """The real device cannot settle this, so a synthetic frame does.

    `0xB125`'s bit 11 is zero, so masking 11 bits and masking 12 give the same answer for this
    G12 -- a mutation to `& 0xFFF` passes every other test here. The authority is the
    instruction: `0x2001557c` is `ubfx r3, r3, #0, #0xb`, eleven bits, and the loader splits the
    same `u16` again at `0x20014666` as `[1] >> 3`, which is bits 11-15. This exercises the
    boundary the captured data leaves untouched.
    """
    def identity(packed):
        payload = packed.to_bytes(2, "little") + bytes([0x1F, 0x30, 0x06, 0x16, 0x34, 0x12])
        return upload.parse_identity(upload.Frame(0.0, 0x19FED89C, payload))

    boundary = identity(0x0925)          # bit 11 set, everything above it clear
    assert boundary.manufacturer == 0x125
    assert boundary.func_instance == 1

    for packed in (0x0000, 0x07FF, 0x0800, 0xB125, 0xFFFF):
        got = identity(packed)
        assert got.manufacturer <= 0x7FF
        assert got.manufacturer | (got.func_instance << 11) == packed, f"{packed:#06x}"


# --------------------------------------------------------------------------------------------
# The panel's QUADSPI image store. These pin facts about `cli.bin` itself, so a corpus change
# or a re-derivation that contradicts them fails rather than drifting.
# --------------------------------------------------------------------------------------------

#: The container magic the loader accepts at a QUADSPI source base (`0x2001528e`). A second
#: accepted value is formed *at that site* by subtracting `0x65800000`, `0xEA000` and `0xD28`
#: (`0x20015298`-`0x200152a0`) -- but it is also stored as a literal twice, at `0x20014d7c` and
#: `0x2001553c`, so both are findable.
QUADSPI_MAGIC = 0xB2285EA0
QUADSPI_MAGIC_ALT = (QUADSPI_MAGIC - 0x65800000 - 0xEA000 - 0xD28) & 0xFFFFFFFF

CLI_BINS = {
    "11.23": ROOT / "firefly-11.23" / "cli.bin",
    "13.6": ROOT / "firefly-13.6" / "cli.bin",
    "14.7": ROOT / "firefly-14.7 (Terrain)" / "cli.bin",
}


def test_the_quadspi_container_magic_is_a_13x_feature():
    """11.23's loader does not carry it, so uploading from QUADSPI arrived with 13.x.

    Only the first magic is a literal; the second is formed by a subtract chain at runtime, so
    searching the file for it would correctly find nothing.
    """
    import struct

    def count(path, value):
        data = path.read_bytes()
        return sum(
            struct.unpack_from("<I", data, o)[0] == value
            for o in range(0, len(data) - 3, 4)
        )

    assert count(CLI_BINS["11.23"], QUADSPI_MAGIC) == 0
    assert count(CLI_BINS["13.6"], QUADSPI_MAGIC) == 3
    assert count(CLI_BINS["14.7"], QUADSPI_MAGIC) == 3
    # Derived from the subtract chain, never hand-copied: two attempts at that arithmetic by
    # hand got it wrong, and this test caught both.
    assert QUADSPI_MAGIC_ALT == 0x4C99B178

    # It is BOTH computed (here) and stored elsewhere -- the subtract chain is how one site
    # forms it, not evidence that it is absent. Written after the test refuted the guess that
    # a computed constant would be missing from the file.
    assert count(CLI_BINS["11.23"], QUADSPI_MAGIC_ALT) == 0
    assert count(CLI_BINS["13.6"], QUADSPI_MAGIC_ALT) == 2
    assert count(CLI_BINS["14.7"], QUADSPI_MAGIC_ALT) == 2


def test_the_loader_is_byte_identical_between_13_6_and_14_7():
    """So every loader finding carries across those two builds for free -- but not to 11.23."""
    assert CLI_BINS["13.6"].read_bytes() == CLI_BINS["14.7"].read_bytes()
    assert CLI_BINS["11.23"].read_bytes() != CLI_BINS["13.6"].read_bytes()
