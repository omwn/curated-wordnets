#!/usr/bin/env python3
"""
update_licenses.py — Populate license / license_url / license_raw fields.

  license     — SPDX identifier (CC-BY-4.0, MIT, WordNet-3.0, …)
  license_url — canonical URL for that license
  license_raw — original string from the XML Lexicon/@license attribute
                (kept so authors can see what their metadata currently says
                 and update it to match the SPDX form)

Sources (in priority order):
  1. license= attribute in the downloaded GWA LMF XML  build/pkg/{id}/{id}.xml
  2. Existing license field in TOML (normalised to SPDX if not already)

Usage:
  uv run python scripts/update_licenses.py           # write in-place
  uv run python scripts/update_licenses.py --check   # dry-run (print to stdout)
  uv run python scripts/update_licenses.py --report  # list changes only
"""

import argparse
import re
import sys
from pathlib import Path

ROOT      = Path(__file__).parent.parent
TOML_PATH = ROOT / "wordnets_found.toml"
PKG_DIR   = ROOT / "build" / "pkg"

# ── SPDX table ────────────────────────────────────────────────────────────────
# (spdx_id, canonical_url)
SPDX = {
    # Creative Commons
    "CC-BY-4.0":        "https://creativecommons.org/licenses/by/4.0/",
    "CC-BY-3.0":        "https://creativecommons.org/licenses/by/3.0/",
    "CC-BY-2.0":        "https://creativecommons.org/licenses/by/2.0/",
    "CC-BY-SA-4.0":     "https://creativecommons.org/licenses/by-sa/4.0/",
    "CC-BY-SA-3.0":     "https://creativecommons.org/licenses/by-sa/3.0/",
    "CC-BY-NC-4.0":     "https://creativecommons.org/licenses/by-nc/4.0/",
    "CC-BY-NC-3.0":     "https://creativecommons.org/licenses/by-nc/3.0/",
    "CC-BY-NC-SA-4.0":  "https://creativecommons.org/licenses/by-nc-sa/4.0/",
    "CC-BY-NC-SA-3.0":  "https://creativecommons.org/licenses/by-nc-sa/3.0/",
    # OSI
    "MIT":              "https://opensource.org/licenses/MIT",
    "Apache-2.0":       "https://www.apache.org/licenses/LICENSE-2.0",
    "GPL-3.0-only":     "https://www.gnu.org/licenses/gpl-3.0.html",
    "GPL-2.0-only":     "https://www.gnu.org/licenses/gpl-2.0.html",
    "BSD-2-Clause":     "https://opensource.org/licenses/BSD-2-Clause",
    # Other recognised
    "CECILL-C":         "https://cecill.info/licences/Licence_CeCILL-C_V1-en.html",
    "WordNet-3.0":      "https://wordnet.princeton.edu/license-and-commercial-use",
    # Non-SPDX (use LicenseRef- prefix)
    "LicenseRef-ODC-BY":       "https://opendatacommons.org/licenses/by/",
    "LicenseRef-plWordNet":    "http://nlp.pwr.wroc.pl/plwordnet/license",
    "LicenseRef-MS-C-NoReD-ND": "",  # PORTULAN restricted — no canonical public URL
}

# ── URL → SPDX ────────────────────────────────────────────────────────────────

def _cc_from_url(path: str, version: str) -> tuple[str, str]:
    """Return (spdx_id, url) for a CC licence parsed from a URL."""
    # path e.g. "by", "by-sa", "by-nc-sa"
    spdx = "CC-" + path.upper()
    if version:
        spdx += f"-{version}"
        url = f"https://creativecommons.org/licenses/{path}/{version}/"
    else:
        url = f"https://creativecommons.org/licenses/{path}/"
    return spdx, url


URL_PATTERNS: list[tuple[str, object]] = [
    # CC versioned
    (r'creativecommons\.org/licenses/(by(?:-[a-z]+)*)/(\d+\.\d+)',
        lambda m: _cc_from_url(m.group(1), m.group(2))),
    # CC unversioned
    (r'creativecommons\.org/licenses/(by(?:-[a-z]+)*)/?$',
        lambda m: _cc_from_url(m.group(1), "")),
    # Apache
    (r'apache\.org/licenses|opensource\.org/licenses/Apache',
        lambda m: ("Apache-2.0", "https://www.apache.org/licenses/LICENSE-2.0")),
    # MIT
    (r'opensource\.org/licenses/MIT|opensource\.org/license/mit',
        lambda m: ("MIT", "https://opensource.org/licenses/MIT")),
    # GPL
    (r'gnu\.org/licenses/gpl-3',
        lambda m: ("GPL-3.0-only", "https://www.gnu.org/licenses/gpl-3.0.html")),
    (r'gnu\.org/licenses/gpl-2',
        lambda m: ("GPL-2.0-only", "https://www.gnu.org/licenses/gpl-2.0.html")),
    # BSD
    (r'opensource\.org/licenses/BSD-2',
        lambda m: ("BSD-2-Clause", "https://opensource.org/licenses/BSD-2-Clause")),
    # CeCILL-C
    (r'cecill\.info',
        lambda m: ("CECILL-C", "https://cecill.info/licences/Licence_CeCILL-C_V1-en.html")),
    # ODC-BY
    (r'opendefinition\.org/licenses/odc-by|opendatacommons\.org/licenses/by',
        lambda m: ("LicenseRef-ODC-BY", "https://opendatacommons.org/licenses/by/")),
    # WordNet
    (r'wordnet\.princeton\.edu',
        lambda m: ("WordNet-3.0", "https://wordnet.princeton.edu/license-and-commercial-use")),
    # plWordNet
    (r'nlp\.pwr\.wroc\.pl/plwordnet',
        lambda m: ("LicenseRef-plWordNet", "http://nlp.pwr.wroc.pl/plwordnet/license")),
]


def url_to_spdx(url: str) -> tuple[str, str] | None:
    for pattern, factory in URL_PATTERNS:
        m = re.search(pattern, url, re.IGNORECASE)
        if m:
            return factory(m)
    return None


# ── Free-text → SPDX ─────────────────────────────────────────────────────────

# Map informal names → SPDX id
LABEL_MAP = {
    # CC patterns normalised
    "cc by 4.0": "CC-BY-4.0", "cc-by-4.0": "CC-BY-4.0",
    "cc by 3.0": "CC-BY-3.0", "cc-by-3.0": "CC-BY-3.0",
    "cc by 2.0": "CC-BY-2.0",
    "cc by sa 4.0": "CC-BY-SA-4.0", "cc by-sa 4.0": "CC-BY-SA-4.0", "cc-by-sa-4.0": "CC-BY-SA-4.0",
    "cc by sa 3.0": "CC-BY-SA-3.0", "cc by-sa 3.0": "CC-BY-SA-3.0", "cc-by-sa-3.0": "CC-BY-SA-3.0",
    "cc by-sa": "CC-BY-SA-4.0",  # unversioned — assume current
    "cc by nc 4.0": "CC-BY-NC-4.0", "cc by-nc 4.0": "CC-BY-NC-4.0", "cc-by-nc-4.0": "CC-BY-NC-4.0",
    "cc by nc 3.0": "CC-BY-NC-3.0", "cc by-nc 3.0": "CC-BY-NC-3.0",
    "cc by-nc": "CC-BY-NC-4.0",
    "cc by nc sa 4.0": "CC-BY-NC-SA-4.0", "cc by-nc-sa 4.0": "CC-BY-NC-SA-4.0",
    "cc by nc sa 3.0": "CC-BY-NC-SA-3.0", "cc by-nc-sa 3.0": "CC-BY-NC-SA-3.0",
    "cc by-nc-sa": "CC-BY-NC-SA-4.0",
    # OSI
    "mit": "MIT",
    "apache 2.0": "Apache-2.0", "apache-2.0": "Apache-2.0", "apache2": "Apache-2.0",
    "gpl-3.0": "GPL-3.0-only", "gpl 3.0": "GPL-3.0-only", "gpl3": "GPL-3.0-only",
    "gpl-2.0": "GPL-2.0-only",
    "bsd-2-clause": "BSD-2-Clause", "bsd 2-clause": "BSD-2-Clause",
    # Other
    "cecill-c": "CECILL-C",
    "wordnet": "WordNet-3.0",
    "odc-by": "LicenseRef-ODC-BY",
    "plwordnet": "LicenseRef-plWordNet",
    "plwordnet-2": "LicenseRef-plWordNet",
    "ms-c-nored-nd": "LicenseRef-MS-C-NoReD-ND",
}


def label_to_spdx(raw: str) -> str | None:
    """Normalise a free-text licence label to an SPDX identifier."""
    key = raw.strip().lower()
    return LABEL_MAP.get(key)


# ── XML extraction ─────────────────────────────────────────────────────────────

def xml_license(wn_id: str) -> str | None:
    xml_path = PKG_DIR / wn_id / f"{wn_id}.xml"
    if not xml_path.exists():
        return None
    try:
        header = xml_path.read_bytes()[:8192].decode("utf-8", errors="replace")
        m = re.search(r'\blicense="([^"]+)"', header)
        if m:
            v = m.group(1).strip()
            return v if v.lower() not in ("", "unknown") else None
    except OSError:
        pass
    return None


# ── TOML block helpers ────────────────────────────────────────────────────────

def get_field(block: str, field: str) -> str | None:
    m = re.search(rf'^{re.escape(field)}\s*=\s*"([^"]*)"', block, re.MULTILINE)
    return m.group(1) if m else None


def set_field(block: str, field: str, value: str) -> str:
    pattern = rf'^{re.escape(field)}\s*=\s*"[^"]*"\n?'
    new_line = f'{field} = "{value}"\n'
    if re.search(pattern, block, re.MULTILINE):
        return re.sub(pattern, new_line, block, flags=re.MULTILINE)
    # Insert after license_url > license > bcp47 > format, whichever exists last
    for anchor in ("license_url", "license", "bcp47", "format", "confidence"):
        m = re.search(rf'^{re.escape(anchor)}\s*=\s*"[^"]*"', block, re.MULTILINE)
        if m:
            nl = block.find('\n', m.end())
            if nl == -1:
                return block + '\n' + new_line
            return block[:nl+1] + new_line + block[nl+1:]
    return block.rstrip('\n') + '\n' + new_line


# ── main ───────────────────────────────────────────────────────────────────────

def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--check",  action="store_true", help="Print result to stdout")
    p.add_argument("--report", action="store_true", help="List changes only")
    args = p.parse_args(argv)

    content = TOML_PATH.read_text(encoding="utf-8")
    parts   = re.split(r'\n(?=\[\[wordnet\]\])', content)
    header  = parts[0]
    blocks  = parts[1:]

    updated = []
    changes = []

    for block in blocks:
        wn_id = get_field(block, "id") or "?"

        toml_lic     = get_field(block, "license")
        toml_url     = get_field(block, "license_url")
        toml_raw     = get_field(block, "license_raw")
        xml_raw      = xml_license(wn_id)

        # ── Step 1: determine SPDX id + url + raw ────────────────────────────
        spdx = toml_lic   # start with existing
        url  = toml_url
        raw  = toml_raw or xml_raw  # prefer already-stored raw over re-extracting

        if xml_raw:
            raw = xml_raw  # always refresh from XML when available
            if xml_raw.startswith("http"):
                parsed = url_to_spdx(xml_raw)
                if parsed:
                    spdx, url = parsed
                else:
                    url  = url or xml_raw  # keep as url fallback
            else:
                # Plain string — normalise
                spdx = label_to_spdx(xml_raw) or spdx

        # If we still only have an informal label in TOML, normalise it
        if spdx and not xml_raw:
            normalised = label_to_spdx(spdx)
            if normalised:
                spdx = normalised

        # Fill URL from SPDX table if missing
        if spdx and not url and spdx in SPDX:
            url = SPDX[spdx]

        # ── Step 2: apply changes ─────────────────────────────────────────────
        if spdx and spdx != toml_lic:
            block = set_field(block, "license", spdx)
            changes.append(f"  {wn_id}: license {toml_lic!r} → {spdx!r}")

        if url and url != toml_url:
            block = set_field(block, "license_url", url)
            changes.append(f"  {wn_id}: license_url → {url}")

        if raw and raw != toml_raw:
            block = set_field(block, "license_raw", raw)
            changes.append(f"  {wn_id}: license_raw = {raw!r}")

        updated.append(block)

    result = "\n".join([header] + updated)

    if args.report or args.check:
        if changes:
            print(f"{len(changes)} changes:")
            for c in changes:
                print(c)
        else:
            print("No changes.")

    if args.check:
        print("\n--- Result (first 200 lines) ---")
        for line in result.splitlines()[:200]:
            print(line)
    elif not args.report:
        TOML_PATH.write_text(result, encoding="utf-8")
        print(f"Updated {TOML_PATH}  ({len([c for c in changes if 'license_raw' not in c and 'license_url' not in c])} license, "
              f"{len([c for c in changes if 'license_url' in c])} url, "
              f"{len([c for c in changes if 'license_raw' in c])} raw changes)")


if __name__ == "__main__":
    sys.exit(main())
