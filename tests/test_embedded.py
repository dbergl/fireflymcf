import pytest

from fireflymcf import container, embedded


def test_every_section_is_classified(corpus):
    """identify() must consider every named section, so nothing is silently skipped."""
    c = container.parse_container(corpus["14.7"]["mcf"])
    named = {s.name for s in c.sections if s.name and s.length}
    considered = set(embedded.classify(corpus["14.7"]["mcf"]))
    assert named == considered


def test_identify_finds_lcd_firmware_and_nothing_else(corpus):
    """What is currently identifiable, and what is not.

    IDENTIFIED: `LCD Firmware`, at base 0x00210000 -- a plain, uncompressed Cortex-M
    image. This test previously asserted identify() returned [] on the whole corpus,
    on Task 4's conclusion that LCD Firmware was stored compressed. Task 6 disproved
    that: Task 4 only ever tried base 0, where the vector table's own handlers fall
    outside a 62 KB image, and read that as evidence of compression. At the real
    base every handler lands inside the payload. The base is proven twice over --
    from the MCF section descriptor's tag-0x40 entry, and from cli.bin's literal
    0x00210400 pointing at the version magic at payload offset 0x400.

    NOT IDENTIFIED: GuiCode, GuiData and G12. They are not Cortex-M images at all
    (GuiCode leads with the magic 0x1A2B3C4D, GuiData and G12 with the 0x2066
    container family), so no candidate base can make them validate. That is correct
    behaviour, not a gap to be closed by loosening DEFAULT_BASES.

    The assertions stay exact -- which sections, which base, which entry -- so that a
    future change to identify() or DEFAULT_BASES that alters what is recognised fails
    loudly here instead of silently drifting.
    """
    for version in ("11.23", "13.6", "14.7"):
        found = embedded.identify(corpus[version]["mcf"])
        assert [img.section_name for img in found] == ["LCD Firmware"], (
            f"{version}: expected LCD Firmware and only LCD Firmware, got "
            f"{[img.section_name for img in found]}")
        lcd = found[0]
        assert lcd.load_addr == 0x00210000
        assert lcd.entry == 0x002104D0
        assert lcd.initial_sp == 0x20050000


def test_lcd_firmware_first_word_is_the_cli_stack_pointer(corpus):
    """LCD Firmware's first word is byte-identical to cli.bin's own initial SP
    (answer_key.CLI_INITIAL_SP = 0x20050000) in every corpus file -- because both run
    on the same MCU: cli.bin region 1 is a RAM-resident updater and LCD Firmware is
    that MCU's application image, based at 0x00210000."""
    for version in ("11.23", "13.6", "14.7"):
        sigs = {s.section_name: s for s in embedded.characterise(corpus[version]["mcf"])}
        lcd = sigs["LCD Firmware"]
        assert lcd.first_words[0] == 0x20050000
        assert lcd.kind == "vector-table-like"


def test_guicode_first_word_is_its_own_magic(corpus):
    """GuiCode carries a deliberate magic, 0x1A2B3C4D, not a plausible stack
    pointer -- it is not a Cortex-M image at all."""
    for version in ("11.23", "13.6", "14.7"):
        sigs = embedded.characterise(corpus[version]["mcf"])
        guicode = [s for s in sigs if s.section_name.startswith("Lyra") and "GuiCode" in s.section_name]
        assert len(guicode) == 1
        assert guicode[0].first_words[0] == 0x1A2B3C4D
        assert guicode[0].kind == "gui-code"


def test_guidata_and_g12_share_the_2066_container_signature(corpus):
    """GuiData and G12 are not raw Cortex-M images either, but they do share a
    format: the low 16 bits of word 0 read 0x2066 in both, in every corpus file."""
    for version in ("11.23", "13.6", "14.7"):
        sigs = embedded.characterise(corpus[version]["mcf"])
        guidata = next(s for s in sigs if "GuiData" in s.section_name)
        g12 = next(s for s in sigs if s.section_name.startswith("G12"))
        assert guidata.first_words[0] & 0xFFFF == 0x2066
        assert g12.first_words[0] & 0xFFFF == 0x2066
        assert guidata.kind == "gui-container"
        assert g12.kind == "gui-container"


def test_build_tag_is_consistent_within_a_file(corpus):
    """The three non-LCD sections (GuiCode, GuiData, G12) share one build tag within
    a given file. NOTE, a correction to the original assumption: this tag is not
    unique per file. 11.23 and 13.6 both carry 0x1234; only 14.7 differs, at 0x0807.
    So the assertion here is "consistent within a file", not "distinct across every
    file" -- confirmed by checking 11.23, which the coordinator had not yet done."""
    for version in ("11.23", "13.6", "14.7"):
        sigs = embedded.characterise(corpus[version]["mcf"])
        tags = {s.build_tag for s in sigs if s.section_name != "LCD Firmware"}
        assert len(tags) == 1, f"{version}: expected one shared build tag, got {tags}"

    tag_1123 = {s.build_tag for s in embedded.characterise(corpus["11.23"]["mcf"])
                if s.section_name != "LCD Firmware"}.pop()
    tag_136 = {s.build_tag for s in embedded.characterise(corpus["13.6"]["mcf"])
               if s.section_name != "LCD Firmware"}.pop()
    tag_147 = {s.build_tag for s in embedded.characterise(corpus["14.7"]["mcf"])
               if s.section_name != "LCD Firmware"}.pop()

    assert tag_1123 == 0x1234
    assert tag_136 == 0x1234
    assert tag_147 == 0x0807
    # 11.23 and 13.6 collide -- documented, not assumed away.
    assert tag_1123 == tag_136
    assert tag_147 != tag_1123


def test_every_named_section_appears_in_characterise(corpus):
    """characterise() must consider every named, non-empty section -- a section it
    cannot classify still appears, with kind='unknown', never silently dropped."""
    for version in ("11.23", "13.6", "14.7"):
        c = container.parse_container(corpus[version]["mcf"])
        named = {s.name for s in c.sections if s.name and s.length}
        sigs = embedded.characterise(corpus[version]["mcf"])
        assert {s.section_name for s in sigs} == named
        assert all(s.kind != "unknown" for s in sigs), (
            f"{version}: every known corpus section should classify as something "
            "other than 'unknown'; an 'unknown' here would mean a real section "
            "went unrecognised")
