"""
Integration tests for download.py — slow, require network access.

These tests actually download and validate small wordnets to verify
the full pipeline works end-to-end.  They are skipped by default.

Run with:
  pytest -m slow tests/test_integration.py
  pytest -m slow tests/test_integration.py -v

Or run all tests including slow ones:
  pytest --run-slow
"""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
import download as dl


# ── fixtures ──────────────────────────────────────────────────────────────

@pytest.fixture
def build_dirs(tmp_path, monkeypatch):
    """Redirect all build/raw/pkg/results paths to a temp directory."""
    monkeypatch.setattr(dl, "BUILD_DIR", tmp_path / "build")
    monkeypatch.setattr(dl, "RAW_DIR",   tmp_path / "build" / "raw")
    monkeypatch.setattr(dl, "PKG_DIR",   tmp_path / "build" / "pkg")
    monkeypatch.setattr(dl, "RESULTS",   tmp_path / "build" / "results.json")
    (tmp_path / "build").mkdir()
    (tmp_path / "build" / "raw").mkdir()
    (tmp_path / "build" / "pkg").mkdir()
    return tmp_path


# ── _install_xml unit test (no network) ──────────────────────────────────

def test_install_xml_copies_to_pkg(tmp_path, monkeypatch):
    """_install_xml should copy the XML to pkg_dir/{id}.xml and keep a copy in raw_dir."""
    monkeypatch.setattr(dl, "ROOT", tmp_path)

    raw_dir = tmp_path / "raw"
    pkg_dir = tmp_path / "pkg"
    raw_dir.mkdir()
    pkg_dir.mkdir()

    # Simulate a downloaded XML file sitting in raw_dir as a _tmp_ file
    tmp_xml = raw_dir / "_tmp_wn.xml"
    tmp_xml.write_text('<LexicalResource xmlns:dc="http://purl.org/dc/elements/1.1/">'
                       '<Lexicon id="test" label="Test" language="en" email="x@x.com"'
                       ' license="CC-BY" version="1.0"/></LexicalResource>')

    entry = {"id": "test-wn", "name": "Test Wordnet"}
    result = dl._install_xml(entry, tmp_xml, raw_dir, pkg_dir, "wn.xml", [])

    pkg_xml = pkg_dir / "test-wn.xml"
    raw_xml = raw_dir / "wn.xml"

    assert pkg_xml.exists(), "pkg/{id}.xml should exist"
    assert raw_xml.exists(), "raw/wn.xml should remain"
    assert "xml" in result
    assert result["format"] == "GWA LMF"


# ── slow integration tests ────────────────────────────────────────────────

@pytest.mark.slow
def test_download_cantonese_wn(build_dirs, monkeypatch):
    """Download Cantonese WordNet (small, plain XML) and check the result."""
    # cantonese-wn has a direct XML file — good for testing plain-XML path
    entry = {
        "id": "cantonese-wn",
        "name": "Cantonese WordNet",
        "bcp47": "yue",
        "format": "GWA LMF",
        "example_file": "https://raw.githubusercontent.com/lmorgadodacosta/CantoneseWN/master/cantonese-wn.xml",
        "confidence": "high",
    }
    status = dl.download_one(entry, force=True)

    assert status["download"] == "ok"
    assert "xml" in status

    pkg_dir = dl.PKG_DIR / "cantonese-wn"
    assert (pkg_dir / "cantonese-wn.xml").exists()
    assert (pkg_dir / "download.log").exists()

    raw_dir = dl.RAW_DIR / "cantonese-wn"
    assert raw_dir.exists()
    # There should be at least one file in raw_dir (the original download)
    assert any(raw_dir.iterdir())


@pytest.mark.slow
def test_validate_cantonese_wn(build_dirs):
    """Download then validate Cantonese WordNet end-to-end."""
    entry = {
        "id": "cantonese-wn",
        "name": "Cantonese WordNet",
        "bcp47": "yue",
        "format": "GWA LMF",
        "example_file": "https://raw.githubusercontent.com/lmorgadodacosta/CantoneseWN/master/cantonese-wn.xml",
        "confidence": "high",
    }
    status = dl.download_one(entry, force=True)
    assert status["download"] == "ok"

    validated = dl.validate_one(entry, status)
    assert validated["validation"] in ("ok", "errors"), \
        f"Unexpected validation status: {validated['validation']}"

    pkg_dir = dl.PKG_DIR / "cantonese-wn"
    assert (pkg_dir / "validation.log").exists()


@pytest.mark.slow
def test_download_ojw_tab_conversion(build_dirs):
    """Download Old Javanese WordNet (OMW 1.0 tab format) and convert to LMF."""
    entry = {
        "id": "ojw",
        "name": "Old Javanese Wordnet",
        "bcp47": "kaw",
        "format": "OMW 1.0 tab",
        "example_file": "https://raw.githubusercontent.com/omwn/ojw/master/wn-data-kaw.tab",
        "license": "wordnet",
        "confidence": "high",
    }
    status = dl.download_one(entry, force=True)

    assert status["download"] == "ok"

    raw_dir = dl.RAW_DIR / "ojw"
    # The original tab file should be in raw_dir
    tab_files = list(raw_dir.glob("*.tab"))
    assert tab_files, "Original .tab file should be kept in raw_dir"

    # If conversion succeeded, pkg should have xml
    pkg_dir = dl.PKG_DIR / "ojw"
    if "xml" in status:
        assert (pkg_dir / "ojw.xml").exists()


@pytest.mark.slow
def test_raw_and_pkg_dirs_are_separate(build_dirs):
    """Verify raw/ and pkg/ contain different things for an archive-based wordnet."""
    entry = {
        "id": "odenet",
        "name": "Open German WordNet",
        "bcp47": "de",
        "format": "GWA LMF",
        "release_url": "https://github.com/hdaSprachtechnologie/odenet/releases/download/v1.4/odenet.zip",
        "confidence": "high",
    }
    status = dl.download_one(entry, force=True)
    if status["download"] != "ok":
        pytest.skip(f"odenet download failed: {status.get('note')}")

    raw_dir = dl.RAW_DIR / "odenet"
    pkg_dir = dl.PKG_DIR / "odenet"

    # raw_dir should contain the original archive
    raw_files = list(raw_dir.iterdir())
    assert raw_files, "raw_dir should not be empty"
    # No extraction temp dirs should remain
    assert not (raw_dir / "_extracted").exists()

    # pkg_dir should contain the XML
    assert (pkg_dir / "odenet.xml").exists()
    assert (pkg_dir / "download.log").exists()
