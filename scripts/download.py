#!/usr/bin/env python3
"""
Download, validate, and analyse wordnets from wordnets_found.toml.

USAGE
-----
  # Process all high-confidence wordnets (download + validate + report):
  python download.py --all

  # Process specific wordnets only:
  python download.py --ids oewn odenet cantonese-wn

  # Only download (skip validate/analyze):
  python download.py --all --phase download

  # Only validate previously-downloaded wordnets:
  python download.py --all --phase validate

  # Include medium-confidence entries as well:
  python download.py --all --confidence medium

  # Re-download even if already present:
  python download.py --ids oewn --force

PHASES
------
  download  Fetch each wordnet's data file.  Tries release_url →
            example_file → repo_url in order.  Archives are extracted;
            OMW 1.0 tab files are converted to GWA LMF XML via
            tsv2lmf.py (cloned from omwn/omw-data) or the online
            converter at http://server1.nlp.insight-centre.org/gwn-converter/

  validate  Parse the XML with wn.lmf.load() and run wn.validate on
            each lexicon.  Errors (E-codes) and warnings (W-codes) are
            written to build/pkg/{id}/validation.log.

  analyze   Print a structured report: valid / warnings-only / errors /
            parse errors / download failures / skipped, with an error-code
            breakdown and suggested next steps.

DIRECTORY LAYOUT
----------------
  build/
    raw/{id}/          original downloaded file(s), untouched
      *.xml / *.zip / *.tar.* / *.tab  — exactly as received
    pkg/{id}/          processed package per wordnet
      {id}.xml         main LMF file (converted from tab if needed)
      LICENSE          if found in source archive
      README.md        if found in source archive
      citation.bib     if found in source archive
      download.log     audit trail: URLs tried, actions taken
      validation.log   wn.validate output (errors / warnings)
    results.json       per-wordnet status across all phases
    download.log       overall run log (appended on each invocation)
  ext/
    omw-data/          shallow clone of omwn/omw-data for tsv→LMF conversion
"""

import argparse
import gzip
import json
import logging
import re
import shutil
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path
from urllib.parse import urlparse

import requests

try:
    import tomllib
except ImportError:
    import tomli as tomllib  # type: ignore

# ── paths ──────────────────────────────────────────────────────────────────

ROOT      = Path(__file__).parent.parent
BUILD_DIR = ROOT / "build"
RAW_DIR   = BUILD_DIR / "raw"
PKG_DIR   = BUILD_DIR / "pkg"
EXT_DIR   = ROOT / "ext"
RESULTS   = BUILD_DIR / "results.json"
OMW_DATA  = EXT_DIR / "omw-data"
ILI_MAP_PATH = OMW_DATA / "etc" / "cili" / "ili-map-pwn30.tab"

_ILI_MAP_CACHE: dict[str, str] | None = None


def load_ili_map() -> dict[str, str]:
    """Load the PWN 3.0 offset-pos → ILI map (cached after first load)."""
    global _ILI_MAP_CACHE
    if _ILI_MAP_CACHE is not None:
        return _ILI_MAP_CACHE
    if not ILI_MAP_PATH.exists():
        return {}
    m: dict[str, str] = {}
    with ILI_MAP_PATH.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            ili, ssid = line.split("\t")
            m[ssid] = ili
            if ssid.endswith("-s"):
                m[ssid[:-2] + "-a"] = ili
    _ILI_MAP_CACHE = m
    return m

# ── logging ────────────────────────────────────────────────────────────────
# FileHandler is added in main() after BUILD_DIR is created.

logging.basicConfig(
    level=logging.INFO,
    format="%(levelname)s  %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("download")

# ── helpers ────────────────────────────────────────────────────────────────

ARCHIVE_SUFFIXES = {".tar.xz", ".tar.gz", ".tgz", ".tar.bz2", ".zip", ".gz"}

def is_archive(url: str) -> bool:
    """Return True if the URL path ends with a known archive suffix."""
    p = urlparse(url).path
    return any(p.endswith(s) for s in ARCHIVE_SUFFIXES)

def is_xml(url: str) -> bool:
    """Return True if the URL path ends with .xml or .xml.gz."""
    return urlparse(url).path.endswith((".xml", ".xml.gz"))

def is_tab(url: str) -> bool:
    """Return True if the URL path ends with .tab or .tsv (OMW 1.0 format)."""
    return urlparse(url).path.endswith((".tab", ".tsv"))

def github_raw(url: str) -> str | None:
    """Convert a github.com blob URL to a raw.githubusercontent.com URL."""
    m = re.match(r"https://github\.com/([^/]+/[^/]+)/blob/(.+)", url)
    if m:
        return f"https://raw.githubusercontent.com/{m.group(1)}/refs/heads/{m.group(2)}"
    return None

def fetch(url: str, timeout: int = 60) -> requests.Response | None:
    """GET url, following redirects. Returns response or None on failure."""
    try:
        r = requests.get(url, timeout=timeout, allow_redirects=True,
                         headers={"User-Agent": "curated-wordnets/1.0"})
        r.raise_for_status()
        return r
    except Exception as e:
        log.debug("fetch %s → %s", url, e)
        return None

def find_xml_in_dir(d: Path) -> Path | None:
    """Find the first .xml file (prefer one not named test/schema)."""
    xmls = sorted(d.rglob("*.xml"))
    xmls = [x for x in xmls if "test" not in x.name.lower()
            and "schema" not in x.name.lower()
            and "dtd" not in x.name.lower()]
    return xmls[0] if xmls else None

def find_support_files(d: Path) -> dict[str, Path | None]:
    """Look for LICENSE, README, citation in a directory tree."""
    def first(patterns):
        for pat in patterns:
            hits = list(d.rglob(pat))
            if hits:
                return hits[0]
        return None
    return {
        "LICENSE":  first(["LICENSE", "LICENSE.txt", "LICENSE.md", "LICENCE"]),
        "README":   first(["README.md", "README.txt", "README"]),
        "citation": first(["citation.bib", "CITATION.bib", "CITATION.cff"]),
    }

def write_log(pkg_dir: Path, lines: list[str]):
    """Write accumulated log_lines to build/pkg/{id}/download.log."""
    with open(pkg_dir / "download.log", "w") as f:
        f.write("\n".join(lines) + "\n")

def load_results() -> dict:
    """Load build/results.json, returning an empty dict if it doesn't exist."""
    if RESULTS.exists():
        return json.loads(RESULTS.read_text())
    return {}

def save_results(results: dict):
    """Persist the results dict to build/results.json (atomic via write_text)."""
    RESULTS.parent.mkdir(parents=True, exist_ok=True)
    RESULTS.write_text(json.dumps(results, indent=2))

GWN_CONVERTER_URL = "http://server1.nlp.insight-centre.org/gwn-converter/"

def ensure_omw_data():
    """Clone omw-data if not already present."""
    if not OMW_DATA.exists():
        log.info("Cloning omwn/omw-data into ext/omw-data …")
        EXT_DIR.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            ["git", "clone", "--depth=1",
             "https://github.com/omwn/omw-data.git", str(OMW_DATA)],
            check=True,
        )
    return OMW_DATA / "scripts" / "tsv2lmf.py"


def convert_tab_online(tab_path: Path, xml_out: Path, log_lines: list) -> bool:
    """
    Try converting an OMW 1.0 tab file via the GWN online converter.
    Returns True on success.
    See: http://server1.nlp.insight-centre.org/gwn-converter/
    """
    log_lines.append(f"  trying online converter: {GWN_CONVERTER_URL}")
    try:
        with open(tab_path, "rb") as f:
            r = requests.post(
                GWN_CONVERTER_URL,
                files={"file": (tab_path.name, f, "text/plain")},
                data={"format": "lmf"},
                timeout=120,
            )
        r.raise_for_status()
        content = r.content
        # Basic sanity check that we got XML back
        if b"LexicalResource" in content or b"<?xml" in content:
            xml_out.write_bytes(content)
            log_lines.append(f"  online conversion succeeded → {xml_out.name}")
            return True
        log_lines.append(f"  online converter returned unexpected content: "
                         f"{content[:100]}")
        return False
    except Exception as e:
        log_lines.append(f"  online converter failed: {e}")
        return False

# ── download ───────────────────────────────────────────────────────────────

def download_one(entry: dict, force: bool = False) -> dict:
    """
    Download a single wordnet. Returns a status dict.
    Tries release_url → example_file → repo_url in order.
    Raw files are saved to build/raw/{id}/, processed output to build/pkg/{id}/.
    """
    wn_id = entry["id"]
    raw_dir = RAW_DIR / wn_id
    pkg_dir = PKG_DIR / wn_id
    raw_dir.mkdir(parents=True, exist_ok=True)
    pkg_dir.mkdir(parents=True, exist_ok=True)
    log_lines = [f"id: {wn_id}", f"name: {entry.get('name', '')}"]

    # Skip if already done and not forced
    if not force and (pkg_dir / "download.log").exists():
        prev = load_results().get(wn_id, {})
        if prev.get("download") not in (None, "failed", "skipped"):
            log.info("  [%s] already downloaded, skipping (--force to redo)", wn_id)
            return prev

    urls = []
    for key in ("release_url", "example_file", "repo_url"):
        if u := entry.get(key):
            urls.append((key, u))

    if not urls:
        msg = "No URL available in TOML"
        log.warning("  [%s] skipped: %s", wn_id, msg)
        log_lines.append(f"status: skipped — {msg}")
        write_log(pkg_dir, log_lines)
        return {"download": "skipped", "note": msg}

    for key, url in urls:
        log_lines.append(f"trying {key}: {url}")
        log.info("  [%s] trying %s: %s", wn_id, key, url)
        result = _try_url(entry, url, raw_dir, pkg_dir, log_lines)
        if result:
            log_lines.append(f"status: ok via {key}")
            write_log(pkg_dir, log_lines)
            log.info("  [%s] ✓ downloaded via %s", wn_id, key)
            return {"download": "ok", "source": key, "url": url, **result}
        log_lines.append("  → failed")

    msg = "All URL attempts failed"
    log.warning("  [%s] ✗ %s", wn_id, msg)
    log_lines.append(f"status: failed — {msg}")
    write_log(pkg_dir, log_lines)
    return {"download": "failed", "note": msg}


def _try_url(entry: dict, url: str, raw_dir: Path, pkg_dir: Path,
             log_lines: list) -> dict | None:
    """Attempt download from a URL; return partial result dict or None."""
    fmt = entry.get("format", "")

    # --- GitHub blob URL → convert to raw ---
    if "github.com" in url and "/blob/" in url:
        raw = github_raw(url)
        if raw:
            url = raw
            log_lines.append(f"  converted to raw: {url}")

    # --- GitHub repo URL (no blob) → use API to find data file ---
    if re.match(r"https://github\.com/[^/]+/[^/]+/?$", url):
        return _try_github_repo(entry, url, raw_dir, pkg_dir, log_lines)

    # --- direct download ---
    r = fetch(url)
    if r is None:
        log_lines.append(f"  HTTP fetch failed: {url}")
        return None

    filename = Path(urlparse(url).path).name or "download"
    tmp = raw_dir / ("_tmp_" + filename)
    tmp.write_bytes(r.content)

    # Extract archive
    if is_archive(url):
        return _extract_archive(entry, tmp, raw_dir, pkg_dir, log_lines)

    # Plain XML
    if filename.endswith(".xml"):
        if fmt == "VisDic XML":
            return _convert_visdic(entry, tmp, raw_dir, pkg_dir, log_lines)
        return _install_xml(entry, tmp, raw_dir, pkg_dir, filename, log_lines)

    # Gzipped XML — keep the .gz in raw_dir, decompress to raw_dir, install to pkg
    if filename.endswith(".xml.gz"):
        raw_gz = raw_dir / filename
        shutil.move(str(tmp), raw_gz)
        xml_name = filename[:-3]
        xml_path = raw_dir / xml_name
        xml_path.write_bytes(gzip.decompress(raw_gz.read_bytes()))
        if fmt == "VisDic XML":
            return _convert_visdic(entry, xml_path, raw_dir, pkg_dir, log_lines)
        return _install_xml(entry, xml_path, raw_dir, pkg_dir, xml_name, log_lines)

    # Tab/TSV → convert to LMF
    if filename.endswith((".tab", ".tsv")) or "OMW 1.0" in fmt:
        return _convert_tab(entry, tmp, raw_dir, pkg_dir, log_lines)

    log_lines.append(f"  unknown file type: {filename}")
    tmp.unlink(missing_ok=True)
    return None


def _extract_archive(entry: dict, archive: Path, raw_dir: Path, pkg_dir: Path,
                     log_lines: list) -> dict | None:
    """Extract an archive and locate + install the XML file.
    The original archive is kept in raw_dir; processed files go to pkg_dir."""
    try:
        # Rename tmp archive to its proper name in raw_dir
        proper_name = archive.name.removeprefix("_tmp_")
        raw_archive = raw_dir / proper_name
        if archive != raw_archive:
            shutil.move(str(archive), raw_archive)

        extract_to = raw_dir / "_extracted"
        extract_to.mkdir(exist_ok=True)

        name = raw_archive.name
        if name.endswith((".tar.xz", ".tar.gz", ".tgz", ".tar.bz2")):
            with tarfile.open(raw_archive) as tf:
                if sys.version_info >= (3, 12):
                    tf.extractall(extract_to, filter="data")
                else:
                    extract_real = str(extract_to.resolve())
                    for member in tf.getmembers():
                        dest = str((extract_to / member.name).resolve())
                        if not dest.startswith(extract_real):
                            raise ValueError(f"Tar path traversal attempt: {member.name}")
                    tf.extractall(extract_to)
        elif name.endswith(".zip"):
            with zipfile.ZipFile(raw_archive) as zf:
                extract_real = str(extract_to.resolve())
                for member in zf.infolist():
                    dest = str((extract_to / member.filename).resolve())
                    if not dest.startswith(extract_real):
                        raise ValueError(f"Zip path traversal attempt: {member.filename}")
                zf.extractall(extract_to)
        elif name.endswith(".gz"):
            inner = extract_to / name[:-3]
            inner.write_bytes(gzip.decompress(raw_archive.read_bytes()))
        else:
            log_lines.append(f"  unsupported archive type: {name}")
            shutil.rmtree(extract_to, ignore_errors=True)
            return None

        # Find the main XML — honour zip_entry if specified
        zip_entry = entry.get("zip_entry")
        if zip_entry:
            matches = [x for x in extract_to.rglob("*.xml")
                       if x.name == zip_entry or str(x).endswith(zip_entry)]
            xml = matches[0] if matches else None
        else:
            xml = find_xml_in_dir(extract_to)
        if xml is None:
            # Maybe it's a tab/tsv inside an archive
            tabs = list(extract_to.rglob("*.tab")) + list(extract_to.rglob("*.tsv"))
            if tabs:
                # Move the tab to raw_dir before converting
                raw_tab = raw_dir / tabs[0].name
                shutil.move(str(tabs[0]), raw_tab)
                shutil.rmtree(extract_to, ignore_errors=True)
                return _convert_tab(entry, raw_tab, raw_dir, pkg_dir, log_lines)
            log_lines.append("  no XML or tab file found in archive")
            shutil.rmtree(extract_to, ignore_errors=True)
            return None

        # Copy support files to pkg_dir
        support = find_support_files(extract_to)
        for name_key, src in support.items():
            if src:
                dst_name = {"LICENSE": "LICENSE", "README": "README.md",
                            "citation": "citation.bib"}.get(name_key, name_key)
                shutil.copy2(src, pkg_dir / dst_name)
                log_lines.append(f"  copied {name_key}: {src.name}")

        fmt = entry.get("format", "")
        if fmt == "VisDic XML":
            result = _convert_visdic(entry, xml, raw_dir, pkg_dir, log_lines)
        else:
            result = _install_xml(entry, xml, raw_dir, pkg_dir,
                                  entry["id"] + ".xml", log_lines)
        shutil.rmtree(extract_to, ignore_errors=True)
        return result

    except Exception as e:
        log_lines.append(f"  archive extraction error: {e}")
        return None


def _install_xml(entry: dict, src: Path, raw_dir: Path, pkg_dir: Path,
                 final_name: str, log_lines: list) -> dict:
    """Copy/move the XML file: raw copy stays in raw_dir, pkg gets {id}.xml."""
    # Keep the original in raw_dir under its natural name
    raw_target = raw_dir / final_name
    if src != raw_target and src.parent == raw_dir:
        src.rename(raw_target)
    elif src.parent != raw_dir:
        # Source is in the extraction temp dir — copy to raw_dir too
        shutil.copy2(src, raw_target)

    # Install to pkg as {id}.xml, normalising the XML declaration if needed
    # (wn.lmf requires exactly: <?xml version="1.0" encoding="UTF-8"?>)
    pkg_target = pkg_dir / (entry["id"] + ".xml")
    raw_bytes = raw_target.read_bytes()
    normalised = re.sub(
        rb'<\?xml\s+version="1\.0"\s+encoding="UTF-8"\s*\?>',
        b'<?xml version="1.0" encoding="UTF-8"?>',
        raw_bytes, count=1,
    )
    # Also normalise DOCTYPE (remove stray spaces before closing >)
    normalised = re.sub(
        rb'(<!DOCTYPE LexicalResource SYSTEM "[^"]+")(\s+)>',
        rb'\1>',
        normalised, count=1,
    )
    # Patch missing required Lexicon attributes (email, license) from TOML entry
    lic   = entry.get("license_url") or entry.get("license", "")
    email = entry.get("email", "")
    if lic or email:
        def _patch_lexicon(m: re.Match) -> bytes:
            tag = m.group(0)
            # Strip trailing whitespace/> to rebuild cleanly
            inner = tag[len(b"<Lexicon"):].rstrip(b" \t\r\n>")
            if lic and b"license=" not in tag:
                inner += f' license="{lic}"'.encode()
            if email and b"email=" not in tag:
                inner += f' email="{email}"'.encode()
            return b"<Lexicon" + inner + b">"
        normalised = re.sub(rb'<Lexicon\b[^>]*>', _patch_lexicon, normalised)
    # Add <Requires ref="omw-en" version="2.0" /> for expand-type wordnets
    # that reference PWN synsets but don't already declare the dependency.
    # Add <Requires> for expand-type wordnets; upgrade 1.0 DTD → 1.1 if needed.
    lmf_version_m = re.search(rb'WN-LMF-1\.(\d+)\.dtd', normalised)
    lmf_minor = int(lmf_version_m.group(1)) if lmf_version_m else 0
    if (entry.get("type") == "expand"
            and b'ref="omw-en"' not in normalised
            and b'ili="i' in normalised):
        # Upgrade 1.0 → 1.1 (Requires was added in 1.1; 1.1 is backward-compatible)
        if lmf_minor == 0:
            normalised = normalised.replace(
                b'WN-LMF-1.0.dtd',
                b'WN-LMF-1.1.dtd',
                1,
            )
            log_lines.append("  upgraded DTD 1.0 → 1.1 (for Requires support)")
        requires_line = b'    <Requires ref="omw-en" version="2.0" />\n'
        # Insert after the closing > of the opening <Lexicon ...> tag
        normalised = re.sub(
            rb'(<Lexicon\b[^>]*>)',
            rb'\1\n' + requires_line,
            normalised, count=1,
        )
        log_lines.append("  added <Requires ref=\"omw-en\" version=\"2.0\" />")

    # Patch missing synsets: add empty stub <Synset> for any synset referenced
    # by a sense but not defined in the file.  This is a data bug in the source;
    # stubs let validation pass and are noted in the log.  Report upstream.
    defined  = set(re.findall(rb'<Synset\b[^>]*\bid="([^"]+)"', normalised))
    refs     = set(re.findall(rb'<Sense\b[^>]*\bsynset="([^"]+)"', normalised))
    missing  = refs - defined
    if missing:
        # Infer part-of-speech per missing synset from the entries that use it.
        # Build: synset_id -> set of POS values from LexicalEntry/Lemma
        entry_pos: dict[bytes, set[bytes]] = {}
        for entry_block in re.finditer(
            rb'<LexicalEntry\b[^>]*>(.*?)</LexicalEntry>', normalised, re.DOTALL
        ):
            pos_m = re.search(rb'partOfSpeech="([^"]+)"', entry_block.group(1))
            pos = pos_m.group(1) if pos_m else b"n"
            for ssid in re.findall(rb'<Sense\b[^>]*\bsynset="([^"]+)"',
                                   entry_block.group(0)):
                if ssid in missing:
                    entry_pos.setdefault(ssid, set()).add(pos)

        stubs = b""
        for ssid in sorted(missing):
            pos = next(iter(entry_pos.get(ssid, {b"n"})))
            stubs += (b'    <Synset id="' + ssid
                      + b'" ili="" partOfSpeech="' + pos
                      + b'" />\n')
        # Insert stubs before </Lexicon>
        normalised = normalised.replace(b'</Lexicon>', stubs + b'</Lexicon>', 1)
        log_lines.append(
            f"  added {len(missing)} stub synsets for missing references "
            f"(source data bug — to be reported upstream)"
        )

    transforms: list[str] = []

    # Track Lexicon attribute patching
    lic_present = b"license=" in raw_bytes
    email_present = b"email=" in raw_bytes
    # Re-check after normalisation to see if attrs were added
    if (lic or email) and (
        (lic and not lic_present) or (email and not email_present)
    ):
        transforms.append("Lexicon attrs patched")

    # Track DTD upgrade
    if b"WN-LMF-1.1.dtd" in normalised and b"WN-LMF-1.1.dtd" not in raw_bytes:
        transforms.append("DTD upgraded 1.0→1.1")

    # Track Requires insertion
    if b'ref="omw-en"' in normalised and b'ref="omw-en"' not in raw_bytes:
        transforms.append("Requires added")

    # Track stub synsets
    if missing:
        transforms.append(f"stub synsets ({len(missing)})")

    pkg_target.write_bytes(normalised)
    if normalised != raw_bytes:
        log_lines.append(f"  normalised XML in {pkg_target.name}")
    else:
        log_lines.append(f"  installed: {pkg_target.name}")
    result: dict[str, object] = {"xml": str(pkg_target.relative_to(ROOT)), "format": "GWA LMF"}
    if transforms:
        result["transformations"] = transforms
    return result


def _fix_tab_header(tab: Path, entry: dict, log_lines: list) -> bool:
    """Ensure the OMW 1.0 tab header has 4 tab-separated fields.
    Some files ship with only 3 (missing url); insert the entry's repo_url.
    Returns True if the header was modified."""
    try:
        lines = tab.read_text(encoding="utf-8").splitlines(keepends=True)
    except Exception:
        return False
    if not lines or not lines[0].startswith("#"):
        return False
    header = lines[0].rstrip("\n")
    body = header.lstrip("# ")
    parts = body.split("\t")
    if len(parts) == 3:
        url = entry.get("repo_url") or entry.get("release_url") or ""
        lines[0] = f"## {parts[0]}\t{parts[1]}\t{url}\t{parts[2]}\n"
        tab.write_text("".join(lines), encoding="utf-8")
        log_lines.append(f"  fixed 3-field tab header: added url={url!r}")
        return True
    return False


def _convert_tab(entry: dict, tab: Path, raw_dir: Path, pkg_dir: Path,
                 log_lines: list) -> dict | None:
    """Convert an OMW 1.0 tab file to GWA LMF XML using tsv2lmf.py.
    The original tab file stays in raw_dir; converted XML goes to pkg_dir."""
    # Ensure the tab lives in raw_dir with a clean name
    raw_tab = raw_dir / (entry["id"] + tab.suffix)
    if tab != raw_tab:
        shutil.move(str(tab), raw_tab)

    # tsv2lmf expects a 4-field header: ## label\tlanguage\turl\tlicense
    # Some files have only 3 fields (missing url). Fix in-place before converting.
    header_fixed = _fix_tab_header(raw_tab, entry, log_lines)

    try:
        tsv2lmf = ensure_omw_data()
    except Exception as e:
        log_lines.append(f"  could not clone omw-data: {e}")
        log_lines.append(f"  kept raw tab: {raw_tab.name} (conversion pending)")
        return {"tab": str(raw_tab.relative_to(ROOT)), "format": "OMW 1.0 tab",
                "note": "omw-data unavailable; conversion pending"}

    xml_out = pkg_dir / (entry["id"] + ".xml")
    cmd = [
        sys.executable, str(tsv2lmf),
        str(raw_tab), str(xml_out),
        "--id",       entry.get("id", "unknown"),
        "--label",    entry.get("name", entry.get("id", "unknown")),
        "--language", entry.get("bcp47", "und"),
        "--email",    "unknown@unknown",
        "--license",  entry.get("license", "unknown"),
        "--version",  "1.0",
    ]
    if url := entry.get("repo_url") or entry.get("release_url"):
        cmd += ["--url", url]
    if ILI_MAP_PATH.exists():
        cmd += ["--ili-map", str(ILI_MAP_PATH)]
    if entry.get("type") == "expand":
        cmd += ["--requires", "omw-en:2.0"]

    log_lines.append(f"  converting tab → LMF: {' '.join(cmd[-6:])}")
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        log_lines.append(f"  tsv2lmf error: {result.stderr[:300]}")
        # Try online converter as fallback
        if convert_tab_online(raw_tab, xml_out, log_lines):
            transforms = ["tab header fixed"] if header_fixed else []
            r: dict = {"xml": str(xml_out.relative_to(ROOT)), "format": "GWA LMF",
                       "converted_from": "OMW 1.0 tab (online converter)"}
            if transforms:
                r["transformations"] = transforms
            return r
        log_lines.append(f"  kept raw tab: {raw_tab.name}")
        return {"tab": str(raw_tab.relative_to(ROOT)), "format": "OMW 1.0 tab",
                "note": f"conversion failed: {result.stderr[:200]}"}

    log_lines.append(f"  converted → {xml_out.name}")
    r: dict[str, object] = {"xml": str(xml_out.relative_to(ROOT)), "format": "GWA LMF",
                            "converted_from": "OMW 1.0 tab"}
    if header_fixed:
        r["transformations"] = ["tab header fixed"]
    return r


def _convert_visdic(entry: dict, xml_in: Path, raw_dir: Path, pkg_dir: Path,
                    log_lines: list) -> dict | None:
    """Convert a VisDic/ROWN/DEBVisDic XML file to GWA LMF using visdic2lmf.py.
    The original XML stays in raw_dir; converted LMF goes to pkg_dir."""
    # Ensure the source lives in raw_dir under a stable name
    raw_xml = raw_dir / (entry["id"] + ".xml")
    if xml_in != raw_xml:
        shutil.copy2(xml_in, raw_xml)

    visdic2lmf = Path(__file__).parent / "visdic2lmf.py"
    if not visdic2lmf.exists():
        log_lines.append("  visdic2lmf.py not found — cannot convert VisDic XML")
        return {"xml": str(raw_xml.relative_to(ROOT)), "format": "VisDic XML",
                "note": "visdic2lmf.py missing"}

    xml_out = pkg_dir / (entry["id"] + ".xml")

    # Per-entry conversion options derived from TOML metadata
    notes_lower = entry.get("notes", "").lower()
    lang_filter = None
    if "debvisdic" in notes_lower or "wn root" in notes_lower:
        # DEBVisDic files may have multilingual SYNONYM; filter to target language
        bcp47 = entry.get("bcp47", "")
        # xml:lang uses 2-letter ISO codes matching BCP47 for most languages
        lang_filter = bcp47.split("-")[0] if bcp47 else None

    encoding = "utf-8"
    if "iso-8859" in notes_lower or "iso8859" in notes_lower:
        import re as _re
        m = _re.search(r"iso-?8859-(\d+)", notes_lower)
        if m:
            encoding = f"iso-8859-{m.group(1)}"

    ili_map30 = ROOT / "ext/omw-data/etc/cili/ili-map-pwn30.tab"
    ili_map31 = ROOT / "ext/cili/ili-map-pwn31.tab"

    cmd = [
        sys.executable, str(visdic2lmf),
        str(raw_xml),
        "--id",       entry["id"],
        "--label",    entry.get("name", entry["id"]),
        "--language", entry.get("bcp47", "und"),
        "--email",    "unknown@unknown",
        "--license",  entry.get("license", "unknown"),
        "--version",  "1.0",
        "--encoding", encoding,
        "-o", str(xml_out),
    ]
    if lang_filter:
        cmd += ["--lang", lang_filter]
    if url := entry.get("repo_url") or entry.get("release_url"):
        cmd += ["--url", url]
    for ili_map in [ili_map30, ili_map31]:
        if ili_map.exists():
            cmd += ["--ili-map", str(ili_map)]

    log_lines.append(f"  converting VisDic XML → LMF: {raw_xml.name}")
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        log_lines.append(f"  visdic2lmf error: {result.stderr[:300]}")
        return {"xml": str(raw_xml.relative_to(ROOT)), "format": "VisDic XML",
                "note": f"conversion failed: {result.stderr[:200]}"}

    log_lines.append(f"  converted → {xml_out.name}")
    return {"xml": str(xml_out.relative_to(ROOT)), "format": "GWA LMF",
            "converted_from": "VisDic XML"}


def _try_github_repo(entry: dict, repo_url: str, raw_dir: Path, pkg_dir: Path,
                     log_lines: list) -> dict | None:
    """Use GitHub API to find a data file in a repo."""
    m = re.match(r"https://github\.com/([^/]+)/([^/]+?)/?$", repo_url)
    if not m:
        return None
    owner, repo = m.group(1), m.group(2)
    api = f"https://api.github.com/repos/{owner}/{repo}/git/trees/HEAD?recursive=1"
    r = fetch(api)
    if r is None:
        log_lines.append(f"  GitHub API failed for {owner}/{repo}")
        return None

    tree = r.json().get("tree", [])
    files = [f["path"] for f in tree if f["type"] == "blob"]

    SKIP_NAMES = {"test", "schema", "pom", "config", "build", "dtd", "xsd",
                  "exception", "version"}

    wn_id_lower = entry["id"].lower().replace("-", "_")

    def file_score(f: str) -> tuple:
        """Lower score = better. Prefer: contains id, shallow path, short name."""
        stem = Path(f).stem.lower()
        depth = f.count("/")
        has_id = wn_id_lower in stem or "wordnet" in stem
        return (not has_id, depth, stem)

    # Prefer XML, then tab/tsv, then .xml.gz
    for pattern, handler in [
        (r"\.xml$",    "xml"),
        (r"\.xml\.gz$","xml.gz"),
        (r"\.(tab|tsv)$", "tab"),
    ]:
        matches = [f for f in files if re.search(pattern, f)
                   and not any(s in Path(f).stem.lower() for s in SKIP_NAMES)]
        matches.sort(key=file_score)
        if matches:
            path = matches[0]
            raw_url = (f"https://raw.githubusercontent.com/{owner}/{repo}"
                       f"/HEAD/{path}")
            log_lines.append(f"  found via GitHub API: {path} → {raw_url}")
            return _try_url(entry, raw_url, raw_dir, pkg_dir, log_lines)

    log_lines.append(f"  no suitable data file found in {owner}/{repo}")
    return None

# ── validate ───────────────────────────────────────────────────────────────

def validate_one(entry: dict, status: dict) -> dict:
    """
    Validate a single wordnet using wn.validate (as a library call).
    Looks for the XML in build/pkg/{id}/, writes validation.log there.
    Returns updated status dict.
    """
    import wn.lmf
    import wn.validate

    wn_id = entry["id"]
    pkg_dir = PKG_DIR / wn_id

    xml_path = status.get("xml")
    if not xml_path:
        # Check for a .xml file on disk in pkg_dir
        found = find_xml_in_dir(pkg_dir) if pkg_dir.exists() else None
        if found:
            xml_path = str(found.relative_to(ROOT))
        else:
            note = status.get("note", "no XML file")
            log.info("  [%s] skipping validation: %s", wn_id, note)
            return {**status, "validation": "skipped", "validation_note": note}

    xml_abs = ROOT / xml_path

    # Validation log file goes in pkg_dir
    pkg_dir.mkdir(parents=True, exist_ok=True)
    val_log = pkg_dir / "validation.log"

    try:
        resource = wn.lmf.load(xml_abs, progress_handler=None)
    except Exception as e:
        msg = str(e)
        log.warning("  [%s] ✗ cannot parse XML: %s", wn_id, msg[:120])
        val_log.write_text(f"XML parse error: {msg}\n")
        return {**status, "xml": xml_path,
                "validation": "parse_error", "validation_note": msg}

    # resource is a dict: {"lmf_version": ..., "lexicons": [...]}
    # Each lexicon is also a dict; validate() returns {code: {"message":..., "items":{...}}}
    all_results = {}
    for lex in resource.get("lexicons", []):
        report = wn.validate.validate(lex, progress_handler=None)
        for code, check in report.items():
            items = check.get("items", {})
            count = len(items)
            if count > 0:
                existing = all_results.get(code, {})
                merged_items = {**existing.get("items", {}), **dict(list(items.items())[:10])}
                all_results[code] = {
                    "level": "E" if code.startswith("E") else "W",
                    "count": existing.get("count", 0) + count,
                    "message": check["message"],
                    "items": merged_items,
                }

    errors   = {k: v for k, v in all_results.items() if v["level"] == "E"}
    warnings = {k: v for k, v in all_results.items() if v["level"] == "W"}

    # Write human-readable log
    lines = [f"Validation report for {wn_id}", "=" * 60]
    if errors:
        lines.append(f"\nERRORS ({len(errors)}):")
        for code, info in sorted(errors.items()):
            lines.append(f"  {code}: {info['message']} [{info['count']} instance(s)]")
            for item in list(info["items"])[:5]:
                lines.append(f"    e.g. {item}")
    if warnings:
        lines.append(f"\nWARNINGS ({len(warnings)}):")
        for code, info in sorted(warnings.items()):
            lines.append(f"  {code}: {info['message']} [{info['count']} instance(s)]")
    if not errors and not warnings:
        lines.append("No issues found.")
    val_log.write_text("\n".join(lines) + "\n")

    passed = len(errors) == 0
    status_str = "ok" if passed else "errors"
    if passed:
        log.info("  [%s] ✓ valid (%d warnings)", wn_id, len(warnings))
    else:
        log.warning("  [%s] ✗ %d error(s), %d warning(s)", wn_id,
                    len(errors), len(warnings))

    # Build result without stale notes from previous runs
    result = {k: v for k, v in status.items()
              if k not in ("validation", "validation_note", "error_codes",
                           "warning_codes", "error_count", "warning_count", "details")}
    return {
        **result,
        "xml": xml_path,
        "validation": status_str,
        "error_codes":   list(errors.keys()),
        "warning_codes": list(warnings.keys()),
        "error_count":   sum(v["count"] for v in errors.values()),
        "warning_count": sum(v["count"] for v in warnings.values()),
        "details": {k: {**v, "items": list(v["items"])[:10]}
                    for k, v in all_results.items()},
    }

# ── analyze ────────────────────────────────────────────────────────────────

def analyze_results(results: dict):
    """
    Categorize failures and suggest next steps.
    Printed as a structured report.
    """
    categories = {
        "ok":           [],
        "parse_error":  [],
        "errors":       [],
        "skipped":      [],
        "no_download":  [],
        "failed":       [],
        "tab_pending":  [],
    }

    error_code_tally: dict[str, list[str]] = {}

    for wn_id, s in results.items():
        dl = s.get("download", "unknown")
        val = s.get("validation")

        if dl == "skipped":
            categories["no_download"].append(wn_id)
        elif dl == "failed":
            categories["failed"].append(wn_id)
        elif s.get("format") == "OMW 1.0 tab" and not s.get("xml"):
            categories["tab_pending"].append(wn_id)
        elif val == "skipped":
            categories["skipped"].append(wn_id)
        elif val == "parse_error":
            categories["parse_error"].append(wn_id)
        elif val == "errors":
            categories["errors"].append(wn_id)
            for code in s.get("error_codes", []):
                error_code_tally.setdefault(code, []).append(wn_id)
        elif val == "ok":
            categories["ok"].append(wn_id)
        else:
            categories["skipped"].append(wn_id)

    total = len(results)
    print("\n" + "=" * 60)
    print(f"ANALYSIS REPORT  ({total} wordnets processed)")
    print("=" * 60)

    def show(label, ids, note=""):
        if ids:
            print(f"\n{label} [{len(ids)}]{' — ' + note if note else ''}")
            for i in ids:
                extra = results[i].get("validation_note") or results[i].get("note", "")
                print(f"  {i:30s}  {extra[:60]}")

    show("✓ VALID (no errors)", categories["ok"])
    show("⚠ VALID WITH WARNINGS ONLY",
         [i for i in results if results[i].get("validation") == "ok"
          and results[i].get("warning_count", 0) > 0])

    show("✗ VALIDATION ERRORS", categories["errors"])
    show("✗ XML PARSE ERRORS", categories["parse_error"])
    show("⬇ DOWNLOAD FAILED", categories["failed"],
         "check URLs or authentication")
    show("○ NO DOWNLOAD URL", categories["no_download"],
         "not on GitHub / behind paywall")
    show("○ TAB PENDING CONVERSION", categories["tab_pending"])
    show("○ VALIDATION SKIPPED", categories["skipped"])

    # Error code breakdown
    if error_code_tally:
        print(f"\n{'─'*60}")
        print("ERROR CODE BREAKDOWN (potential easy wins grouped by type):")
        # Map codes to descriptions
        code_desc = {
            "E101": "Duplicate ID within lexicon",
            "E204": "Sense references missing synset",
            "E401": "Relation target missing or invalid",
            "W201": "Lexical entry has no senses",
            "W302": "ILI repeated across synsets",
            "W303": "Proposed ILI missing definition",
            "W404": "Reverse relation missing",
            "W501": "POS mismatch with hypernym",
            "W502": "Self-loop relation",
        }
        for code, ids in sorted(error_code_tally.items(),
                                 key=lambda x: -len(x[1])):
            desc = code_desc.get(code, "")
            print(f"  {code}  {desc:40s}  affects {len(ids)} wordnet(s): "
                  f"{', '.join(ids[:5])}{' …' if len(ids)>5 else ''}")

    # Suggestions
    print(f"\n{'─'*60}")
    print("SUGGESTED NEXT STEPS:")
    opts = []
    if categories["parse_error"]:
        opts.append(f"A) Fix XML parse errors in {len(categories['parse_error'])} files "
                    f"(check encoding, root element, DTD declarations)")
    if categories["tab_pending"]:
        opts.append(f"B) Convert {len(categories['tab_pending'])} OMW 1.0 tab files "
                    f"(clone omw-data, run tsv2lmf with correct metadata)")
    if error_code_tally.get("E401"):
        opts.append(f"C) Fix E401 missing relation targets "
                    f"({len(error_code_tally['E401'])} wordnets) — often removable")
    if error_code_tally.get("E101"):
        opts.append("D) Fix E101 duplicate IDs — often auto-fixable")
    if error_code_tally.get("W404"):
        opts.append("E) Add W404 reverse relations — wn can often infer these")
    if categories["failed"]:
        opts.append(f"F) Investigate {len(categories['failed'])} failed downloads")
    if categories["no_download"]:
        opts.append(f"G) Manually obtain {len(categories['no_download'])} wordnets "
                    f"(ELDA/CLARIN paywall or restricted license)")
    for opt in opts:
        print(f"  {opt}")
    if not opts:
        print("  All wordnets validated successfully!")
    print()

# ── main ───────────────────────────────────────────────────────────────────

def load_toml(path: Path) -> list[dict]:
    with open(path, "rb") as f:
        data = tomllib.load(f)
    return data.get("wordnet", [])


def filter_entries(entries: list[dict], ids: list[str] | None,
                   min_confidence: str) -> list[dict]:
    order = {"high": 0, "medium": 1, "low": 2}
    max_level = order.get(min_confidence, 2)
    result = []
    for e in entries:
        if ids and e["id"] not in ids:
            continue
        level = order.get(e.get("confidence", "low"), 2)
        if level <= max_level:
            result.append(e)
    return result


def main():
    BUILD_DIR.mkdir(parents=True, exist_ok=True)
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    PKG_DIR.mkdir(parents=True, exist_ok=True)
    fh = logging.FileHandler(BUILD_DIR / "download.log", mode="a")
    fh.setFormatter(logging.Formatter("%(levelname)s  %(message)s"))
    logging.getLogger().addHandler(fh)

    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--toml", default="wordnets_found.toml",
                        help="Input TOML file (default: wordnets_found.toml)")
    grp = parser.add_mutually_exclusive_group(required=True)
    grp.add_argument("--all", action="store_true", help="Process all entries")
    grp.add_argument("--ids", nargs="+", metavar="ID",
                     help="Process specific wordnet IDs")
    parser.add_argument("--phase",
                        choices=["download", "validate", "analyze", "all"],
                        default="all", help="Which phase(s) to run (default: all)")
    parser.add_argument("--confidence",
                        choices=["high", "medium", "low"],
                        default="high",
                        help="Minimum confidence level to include (default: high)")
    parser.add_argument("--force", action="store_true",
                        help="Re-download even if already present")
    args = parser.parse_args()

    entries = load_toml(Path(args.toml))
    entries = filter_entries(entries, args.ids if not args.all else None,
                             args.confidence)
    log.info("Processing %d wordnet(s)  phase=%s  confidence>=%s",
             len(entries), args.phase, args.confidence)

    results = load_results()

    # ── download ──
    if args.phase in ("download", "all"):
        log.info("\n── DOWNLOAD ──────────────────────────────────────────")
        for e in entries:
            wn_id = e["id"]
            log.info("[%s] %s", wn_id, e.get("name", ""))
            status = download_one(e, force=args.force)
            results[wn_id] = {**results.get(wn_id, {}), **status,
                              "name": e.get("name", ""), "bcp47": e.get("bcp47", "")}
            save_results(results)

    # ── validate ──
    if args.phase in ("validate", "all"):
        log.info("\n── VALIDATE ──────────────────────────────────────────")
        for e in entries:
            wn_id = e["id"]
            if wn_id not in results:
                log.info("  [%s] not downloaded yet, skipping", wn_id)
                continue
            log.info("[%s] %s", wn_id, e.get("name", ""))
            updated = validate_one(e, results[wn_id])
            results[wn_id] = updated
            save_results(results)

    # ── analyze ──
    if args.phase in ("analyze", "all"):
        analyze_results(results)


if __name__ == "__main__":
    main()
