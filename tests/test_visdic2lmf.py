"""
Unit tests for visdic2lmf.py — fast, no network required.

Covers the KeNet-specific additions:
  - Uppercase ILR/SR relation types (e.g. HYPERNYM, ANTONYM)
  - <EXAMPLE> tag with pipe-separated multiple examples
  - SYNONYM ILR → ILI assignment from an external ILI map
  - load_ili_map: basic loading and satellite-adjective normalisation
"""

import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

import visdic2lmf as v


# ── shared helpers ────────────────────────────────────────────────────────

def convert_xml(tmp_path, xml_str: str, ilimap: dict | None = None,
                wn_id: str = "test") -> ET.Element:
    """Write xml_str to a temp file, run convert(), return the Lexicon element."""
    inp = tmp_path / "input.xml"
    out = tmp_path / "output.xml"
    inp.write_text(xml_str, encoding="utf-8")
    v.convert(
        input_path=inp,
        output_path=out,
        wn_id=wn_id,
        label="Test WN",
        language="tr",
        email="test@example.com",
        license_="CC-BY-4.0",
        ilimap=ilimap or {},
        keep_empty=False,
    )
    root = ET.parse(out).getroot()
    return root.find("Lexicon")


def synset(lex: ET.Element, ss_id: str) -> ET.Element:
    for ss in lex.findall("Synset"):
        if ss.get("id") == ss_id:
            return ss
    raise KeyError(ss_id)


def rel_types(lex: ET.Element, ss_id: str) -> list[str]:
    return [r.get("relType") for r in synset(lex, ss_id).findall("SynsetRelation")]


def examples(lex: ET.Element, ss_id: str) -> list[str]:
    return [e.text for e in synset(lex, ss_id).findall("Example")]


# ── uppercase relation types ──────────────────────────────────────────────

UPPERCASE_RELS = """\
<SYNSETS>
<SYNSET>
  <ID>TUR10-0000001</ID><POS>n</POS>
  <SYNONYM><LITERAL>su<SENSE>1</SENSE></LITERAL></SYNONYM>
  <SR>TUR10-0000002<TYPE>HYPERNYM</TYPE></SR>
  <SR>TUR10-0000003<TYPE>ANTONYM</TYPE></SR>
  <SR>TUR10-0000004<TYPE>DERIVATION_RELATED</TYPE></SR>
  <DEF>Water</DEF>
</SYNSET>
<SYNSET><ID>TUR10-0000002</ID><POS>n</POS>
  <SYNONYM><LITERAL>sıvı<SENSE>1</SENSE></LITERAL></SYNONYM><DEF>Liquid</DEF></SYNSET>
<SYNSET><ID>TUR10-0000003</ID><POS>n</POS>
  <SYNONYM><LITERAL>kuru<SENSE>1</SENSE></LITERAL></SYNONYM><DEF>Dry</DEF></SYNSET>
<SYNSET><ID>TUR10-0000004</ID><POS>n</POS>
  <SYNONYM><LITERAL>ıslak<SENSE>1</SENSE></LITERAL></SYNONYM><DEF>Wet</DEF></SYNSET>
</SYNSETS>"""

class TestUppercaseRelations:
    @pytest.fixture(autouse=True)
    def lex(self, tmp_path):
        self._lex = convert_xml(tmp_path, UPPERCASE_RELS)

    def test_hypernym_captured(self):
        assert "hypernym" in rel_types(self._lex, "test-TUR10_0000001-n")

    def test_antonym_captured(self):
        assert "antonym" in rel_types(self._lex, "test-TUR10_0000001-n")

    def test_derivation_related_captured(self):
        assert "derivation" in rel_types(self._lex, "test-TUR10_0000001-n")

    def test_three_relations_total(self):
        assert len(rel_types(self._lex, "test-TUR10_0000001-n")) == 3


# ── <EXAMPLE> tag with pipe splitting ─────────────────────────────────────

EXAMPLE_XML = """\
<SYNSETS>
<SYNSET>
  <ID>TUR10-0000001</ID><POS>n</POS>
  <SYNONYM><LITERAL>su<SENSE>1</SENSE></LITERAL></SYNONYM>
  <DEF>Water</DEF>
  <EXAMPLE>Su akıyor.|Bardak su içtim.|Deniz tuzlu sudur.</EXAMPLE>
</SYNSET>
<SYNSET>
  <ID>TUR10-0000002</ID><POS>n</POS>
  <SYNONYM><LITERAL>hava<SENSE>1</SENSE></LITERAL></SYNONYM>
  <DEF>Air</DEF>
  <EXAMPLE>Temiz hava soluyoruz.</EXAMPLE>
</SYNSET>
<SYNSET>
  <ID>TUR10-0000003</ID><POS>n</POS>
  <SYNONYM><LITERAL>ateş<SENSE>1</SENSE></LITERAL></SYNONYM>
  <DEF>Fire</DEF>
</SYNSET>
<SYNSET>
  <ID>TUR10-0000004</ID><POS>n</POS>
  <SYNONYM><LITERAL>yağmur<SENSE>1</SENSE></LITERAL></SYNONYM>
  <DEF>Rain</DEF>
  <USAGE>Yağmur yağıyor.</USAGE>
</SYNSET>
</SYNSETS>"""

class TestExampleTag:
    @pytest.fixture(autouse=True)
    def lex(self, tmp_path):
        self._lex = convert_xml(tmp_path, EXAMPLE_XML)

    def test_pipe_gives_three_examples(self):
        assert len(examples(self._lex, "test-TUR10_0000001-n")) == 3

    def test_pipe_examples_contain_no_pipe(self):
        assert all("|" not in e for e in examples(self._lex, "test-TUR10_0000001-n"))

    def test_single_example_preserved(self):
        exs = examples(self._lex, "test-TUR10_0000002-n")
        assert exs == ["Temiz hava soluyoruz."]

    def test_no_example_gives_empty(self):
        assert examples(self._lex, "test-TUR10_0000003-n") == []

    def test_usage_tag_still_works(self):
        exs = examples(self._lex, "test-TUR10_0000004-n")
        assert exs == ["Yağmur yağıyor."]


# ── SYNONYM ILR → ILI assignment ──────────────────────────────────────────

SYNONYM_ILR_XML = """\
<SYNSETS>
<SYNSET>
  <ID>TUR10-0000001</ID><POS>n</POS>
  <SYNONYM><LITERAL>su<SENSE>1</SENSE></LITERAL></SYNONYM>
  <ILR>ENG31-09633926-n<TYPE>SYNONYM</TYPE></ILR>
  <DEF>Water</DEF>
</SYNSET>
<SYNSET>
  <ID>TUR10-0000002</ID><POS>n</POS>
  <SYNONYM><LITERAL>kuru<SENSE>1</SENSE></LITERAL></SYNONYM>
  <DEF>No ILI link</DEF>
</SYNSET>
</SYNSETS>"""

class TestSynonymIliExtraction:
    def test_ili_assigned_when_map_matches(self, tmp_path):
        lex = convert_xml(tmp_path, SYNONYM_ILR_XML, ilimap={"09633926-n": "ili:i99999"})
        assert synset(lex, "test-TUR10_0000001-n").get("ili") == "ili:i99999"

    def test_ili_empty_when_map_misses(self, tmp_path):
        lex = convert_xml(tmp_path, SYNONYM_ILR_XML, ilimap={})
        assert synset(lex, "test-TUR10_0000001-n").get("ili", "") == ""

    def test_synonym_not_added_as_synset_relation(self, tmp_path):
        lex = convert_xml(tmp_path, SYNONYM_ILR_XML, ilimap={"09633926-n": "ili:i99999"})
        assert "synonym" not in rel_types(lex, "test-TUR10_0000001-n")

    def test_synset_without_synonym_ilr_has_no_ili(self, tmp_path):
        lex = convert_xml(tmp_path, SYNONYM_ILR_XML, ilimap={"09633926-n": "ili:i99999"})
        assert synset(lex, "test-TUR10_0000002-n").get("ili", "") == ""


# ── load_ili_map ──────────────────────────────────────────────────────────

class TestLoadIliMap:
    def test_basic_load(self, tmp_path):
        f = tmp_path / "map.tab"
        f.write_text("ili:i1\t00001740-a\nili:i2\t00002098-a\n")
        m = v.load_ili_map(str(f))
        assert m["00001740-a"] == "ili:i1"
        assert m["00002098-a"] == "ili:i2"

    def test_satellite_adjective_normalised(self, tmp_path):
        # -s entries are also indexed under -a
        f = tmp_path / "map.tab"
        f.write_text("ili:i42\t01234567-s\n")
        m = v.load_ili_map(str(f))
        assert m["01234567-a"] == "ili:i42"

    def test_two_maps_merged(self, tmp_path):
        f30 = tmp_path / "pwn30.tab"
        f31 = tmp_path / "pwn31.tab"
        f30.write_text("ili:i1\t00001740-a\n")
        f31.write_text("ili:i2\t99999999-n\n")
        ilimap: dict[str, str] = {}
        for f in [f30, f31]:
            ilimap.update(v.load_ili_map(str(f)))
        assert ilimap["00001740-a"] == "ili:i1"
        assert ilimap["99999999-n"] == "ili:i2"

    def test_later_map_wins_on_conflict(self, tmp_path):
        f1 = tmp_path / "a.tab"
        f2 = tmp_path / "b.tab"
        f1.write_text("ili:i1\t00001740-a\n")
        f2.write_text("ili:i999\t00001740-a\n")
        ilimap: dict[str, str] = {}
        ilimap.update(v.load_ili_map(str(f1)))
        ilimap.update(v.load_ili_map(str(f2)))
        assert ilimap["00001740-a"] == "ili:i999"
