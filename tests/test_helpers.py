"""
Unit tests for download.py helper functions.

These tests are fast and require no network access or real wordnet files.
Run with:  pytest tests/test_helpers.py

Integration tests (slow, needs network) are in test_integration.py and are
skipped by default.  Use:  pytest --run-slow  to run them.
"""


# scripts/ is added to sys.path by conftest.py
import download as dl


# ── URL classifier helpers ────────────────────────────────────────────────

class TestIsArchive:
    def test_tar_gz(self):
        assert dl.is_archive("https://example.com/file.tar.gz")

    def test_tar_xz(self):
        assert dl.is_archive("https://example.com/release.tar.xz")

    def test_zip(self):
        assert dl.is_archive("https://example.com/data.zip")

    def test_tgz(self):
        assert dl.is_archive("https://example.com/pkg.tgz")

    def test_gz(self):
        assert dl.is_archive("https://example.com/wn.xml.gz")

    def test_xml_not_archive(self):
        assert not dl.is_archive("https://example.com/wn.xml")

    def test_tab_not_archive(self):
        assert not dl.is_archive("https://example.com/wn.tab")

    def test_query_string_ignored(self):
        # Only path extension matters
        assert dl.is_archive("https://example.com/file.zip?token=abc")


class TestIsXml:
    def test_xml(self):
        assert dl.is_xml("https://example.com/wordnet.xml")

    def test_xml_gz(self):
        assert dl.is_xml("https://example.com/wordnet.xml.gz")

    def test_tab_not_xml(self):
        assert not dl.is_xml("https://example.com/wordnet.tab")

    def test_zip_not_xml(self):
        assert not dl.is_xml("https://example.com/wordnet.zip")


class TestIsTab:
    def test_tab(self):
        assert dl.is_tab("https://example.com/wn.tab")

    def test_tsv(self):
        assert dl.is_tab("https://example.com/wn.tsv")

    def test_xml_not_tab(self):
        assert not dl.is_tab("https://example.com/wn.xml")


# ── github_raw ────────────────────────────────────────────────────────────

class TestGithubRaw:
    def test_blob_conversion(self):
        url = "https://github.com/owner/repo/blob/main/data/wn.xml"
        raw = dl.github_raw(url)
        assert raw == "https://raw.githubusercontent.com/owner/repo/refs/heads/main/data/wn.xml"

    def test_non_blob_returns_none(self):
        assert dl.github_raw("https://github.com/owner/repo") is None
        assert dl.github_raw("https://github.com/owner/repo/tree/main") is None

    def test_raw_url_returns_none(self):
        assert dl.github_raw("https://raw.githubusercontent.com/owner/repo/main/f.xml") is None

    def test_nested_path(self):
        url = "https://github.com/omwn/omw-data/blob/master/data/eng/wn31.xml"
        raw = dl.github_raw(url)
        assert "raw.githubusercontent.com" in raw
        assert "master/data/eng/wn31.xml" in raw


# ── filter_entries ────────────────────────────────────────────────────────

ENTRIES = [
    {"id": "a", "confidence": "high"},
    {"id": "b", "confidence": "medium"},
    {"id": "c", "confidence": "low"},
    {"id": "d"},  # no confidence field → treated as "low"
]

class TestFilterEntries:
    def test_high_only(self):
        result = dl.filter_entries(ENTRIES, ids=None, min_confidence="high")
        assert [e["id"] for e in result] == ["a"]

    def test_medium_includes_high(self):
        result = dl.filter_entries(ENTRIES, ids=None, min_confidence="medium")
        assert {e["id"] for e in result} == {"a", "b"}

    def test_low_includes_all(self):
        result = dl.filter_entries(ENTRIES, ids=None, min_confidence="low")
        assert len(result) == 4

    def test_id_filter(self):
        result = dl.filter_entries(ENTRIES, ids=["b", "c"], min_confidence="low")
        assert {e["id"] for e in result} == {"b", "c"}

    def test_id_filter_with_confidence(self):
        # ID in list but confidence too low → excluded
        result = dl.filter_entries(ENTRIES, ids=["a", "c"], min_confidence="high")
        assert [e["id"] for e in result] == ["a"]

    def test_empty_ids_list(self):
        # ids=[] is falsy — treated same as ids=None (no filter applied)
        result = dl.filter_entries(ENTRIES, ids=[], min_confidence="low")
        assert len(result) == 4


# ── find_xml_in_dir ───────────────────────────────────────────────────────

class TestFindXmlInDir:
    def test_finds_xml(self, tmp_path):
        (tmp_path / "wordnet.xml").write_text("<LexicalResource/>")
        assert dl.find_xml_in_dir(tmp_path) == tmp_path / "wordnet.xml"

    def test_skips_test_xml(self, tmp_path):
        (tmp_path / "test_data.xml").write_text("<test/>")
        assert dl.find_xml_in_dir(tmp_path) is None

    def test_skips_schema_xml(self, tmp_path):
        (tmp_path / "schema.xml").write_text("<xs:schema/>")
        assert dl.find_xml_in_dir(tmp_path) is None

    def test_prefers_non_test_xml(self, tmp_path):
        (tmp_path / "test.xml").write_text("<test/>")
        (tmp_path / "wn.xml").write_text("<LexicalResource/>")
        assert dl.find_xml_in_dir(tmp_path) == tmp_path / "wn.xml"

    def test_nested_xml(self, tmp_path):
        sub = tmp_path / "data"
        sub.mkdir()
        (sub / "wn.xml").write_text("<LexicalResource/>")
        assert dl.find_xml_in_dir(tmp_path) == sub / "wn.xml"

    def test_empty_dir(self, tmp_path):
        assert dl.find_xml_in_dir(tmp_path) is None


# ── find_support_files ────────────────────────────────────────────────────

class TestFindSupportFiles:
    def test_finds_license(self, tmp_path):
        (tmp_path / "LICENSE").write_text("MIT")
        result = dl.find_support_files(tmp_path)
        assert result["LICENSE"] == tmp_path / "LICENSE"

    def test_finds_readme_md(self, tmp_path):
        (tmp_path / "README.md").write_text("# Wordnet")
        result = dl.find_support_files(tmp_path)
        assert result["README"] == tmp_path / "README.md"

    def test_finds_citation_bib(self, tmp_path):
        (tmp_path / "citation.bib").write_text("@article{...}")
        result = dl.find_support_files(tmp_path)
        assert result["citation"] == tmp_path / "citation.bib"

    def test_missing_files_return_none(self, tmp_path):
        result = dl.find_support_files(tmp_path)
        assert result["LICENSE"] is None
        assert result["README"] is None
        assert result["citation"] is None

    def test_nested_license(self, tmp_path):
        sub = tmp_path / "docs"
        sub.mkdir()
        (sub / "LICENSE.txt").write_text("Apache-2.0")
        result = dl.find_support_files(tmp_path)
        assert result["LICENSE"] == sub / "LICENSE.txt"
