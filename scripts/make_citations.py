#!/usr/bin/env python3
"""
make_citations.py — Generate citations/{id}.bib for every wordnet.

Citation sources (in priority order):
  1. bib     field in wordnets_found.toml — raw BibTeX string
  2. acl_ids field in wordnets_found.toml — list of ACL Anthology paper IDs;
             fetches https://aclanthology.org/{id}.bib for each
  3. citation= attribute in the downloaded GWA LMF XML (embedded as a comment)
  4. Minimal stub

Usage:
  uv run python scripts/make_citations.py
  uv run python scripts/make_citations.py --id oewn
  uv run python scripts/make_citations.py --no-fetch   # skip network
"""

import argparse
import html
import re
import sys
import urllib.request
from pathlib import Path

import tomllib

ROOT          = Path(__file__).parent.parent
TOML_PATH     = ROOT / "wordnets_found.toml"
CITATIONS_DIR = ROOT / "citations"
PKG_DIR       = ROOT / "build" / "pkg"


# ── network ────────────────────────────────────────────────────────────────

def fetch_acl_bib(paper_id: str) -> str | None:
    url = f"https://aclanthology.org/{paper_id}.bib"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "make_citations/1.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.read().decode("utf-8").strip()
    except Exception as e:
        print(f"  [warn] failed to fetch ACL {paper_id}: {e}", file=sys.stderr)
        return None


# ── XML citation extraction ────────────────────────────────────────────────

def extract_xml_citation(wn_id: str) -> str | None:
    xml_path = PKG_DIR / wn_id / f"{wn_id}.xml"
    if not xml_path.exists():
        return None
    try:
        header = xml_path.read_bytes()[:4096].decode("utf-8", errors="replace")
        m = re.search(r'\bcitation="([^"]+)"', header)
        if m:
            return html.unescape(m.group(1)).strip()
    except OSError:
        pass
    return None


# ── stub ───────────────────────────────────────────────────────────────────

def make_stub(entry: dict, xml_citation: str | None = None) -> str:
    wn_id = entry["id"]
    name  = entry.get("name", wn_id)
    url   = entry.get("repo_url") or entry.get("release_url") or ""
    lang  = entry.get("language", "")
    if xml_citation:
        note   = f"\n% FROM XML citation field:\n%   {xml_citation}\n"
        status = "stub — see XML citation above"
    else:
        note   = ""
        status = "stub: no publication found"
    return (
        f"% HANDMADE ({status}) for {wn_id}{note}\n"
        f"@misc{{{wn_id},\n"
        f"  title  = {{{name}}},\n"
        f"  author = {{TODO}},\n"
        f"  year   = {{XXXX}},\n"
        f"  url    = {{{url}}},\n"
        f"  note   = {{Wordnet for {lang}. Citation not yet identified.}}\n"
        f"}}"
    )


# ── main citation builder ──────────────────────────────────────────────────

def make_citation(entry: dict, fetch_acl: bool = True) -> tuple[str, str]:
    """Return (bib_text, source_label)."""
    wn_id = entry["id"]

    # 1. bib field in TOML
    if bib := entry.get("bib"):
        return bib.strip(), "toml-bib"

    # 2. acl_ids field in TOML
    if acl_ids := entry.get("acl_ids"):
        if fetch_acl:
            bibs = [b for aid in acl_ids if (b := fetch_acl_bib(aid))]
            if bibs:
                return "\n\n".join(bibs), "ACL"
        else:
            return (
                f"% ACL IDs: {acl_ids}  (run without --no-fetch to populate)\n"
                f"% STUB (no-fetch mode)\n@misc{{{wn_id}}}",
                "ACL-placeholder",
            )

    # 3. XML citation comment
    xml_citation = extract_xml_citation(wn_id)

    # 4. Stub
    return make_stub(entry, xml_citation), "stub"


# ── CLI ────────────────────────────────────────────────────────────────────

def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--id",       help="Only process this wordnet ID")
    p.add_argument("--no-fetch", action="store_true",
                   help="Skip ACL Anthology network requests")
    args = p.parse_args(argv)

    CITATIONS_DIR.mkdir(exist_ok=True)

    with open(TOML_PATH, "rb") as f:
        entries = tomllib.load(f).get("wordnet", [])

    if args.id:
        entries = [e for e in entries if e["id"] == args.id]
        if not entries:
            print(f"No entry with id={args.id!r}", file=sys.stderr)
            return 1

    seen: set[str] = set()
    ok = skip = 0
    for entry in entries:
        wn_id = entry["id"]
        if wn_id in seen:
            skip += 1
            continue
        seen.add(wn_id)

        bib, source = make_citation(entry, fetch_acl=not args.no_fetch)
        (CITATIONS_DIR / f"{wn_id}.bib").write_text(bib + "\n", encoding="utf-8")
        print(f"  [{source}] {wn_id}")
        ok += 1

    print(f"\nWrote {ok} citation files to {CITATIONS_DIR}/  ({skip} duplicates skipped)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
