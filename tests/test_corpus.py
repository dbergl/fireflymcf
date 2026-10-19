"""The corpus is the test suite. These assertions guard against a corrupted or
partially-copied working tree before any parsing work is trusted."""
from tests import answer_key


def test_all_six_corpus_files_load(corpus):
    assert set(corpus) == {"11.23", "13.6", "14.7"}
    for version, files in corpus.items():
        assert len(files["mcf"]) > 0, f"{version} mcf.bin is empty"
        assert len(files["cli"]) > 0, f"{version} cli.bin is empty"


def test_corpus_file_sizes(corpus):
    assert len(corpus["11.23"]["cli"]) == 156212
    assert len(corpus["11.23"]["mcf"]) == 905408
    assert len(corpus["13.6"]["cli"]) == 153392
    assert len(corpus["13.6"]["mcf"]) == 1067000
    assert len(corpus["14.7"]["cli"]) == 153392
    assert len(corpus["14.7"]["mcf"]) == 800752


def test_136_and_147_cli_are_byte_identical(corpus):
    """The CLI firmware is a generic interpreter; the version lives in the MCF.
    If this ever fails, the corpus changed and every conclusion needs rechecking."""
    assert corpus["13.6"]["cli"] == corpus["14.7"]["cli"]
    assert corpus["11.23"]["cli"] != corpus["13.6"]["cli"]


def test_mcf_magic_is_uniform(corpus):
    for version, files in corpus.items():
        assert files["mcf"][:4] == bytes.fromhex("12b953fd"), version


def test_cli_magic_is_uniform(corpus):
    for version, files in corpus.items():
        assert files["cli"][:4] == bytes.fromhex("2e101bc1"), version


def test_answer_key_is_internally_consistent():
    assert len(answer_key.FLOORPLANS) == 10
    assert sorted(answer_key.FLOORPLANS) == list(range(1, 11))
    assert answer_key.FLOORPLANS[8] == "WD"
    assert len(answer_key.OPTIONS) == 6
    selectors = [sel for _, sel in answer_key.OPTIONS]
    assert len(set(selectors)) == 6, "option selectors must be distinct"
    assert answer_key.CHANNELS[25] == "CARGO LTS / BATH LTS"
