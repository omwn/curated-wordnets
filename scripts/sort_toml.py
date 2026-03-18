#!/usr/bin/env python3
"""
sort_toml.py — Sort wordnets_found.toml entries by language (then id),
               strip stale section comments, regenerate language headers,
               and rename supersedes → superseded-by (reversing direction).

Usage:
  uv run python scripts/sort_toml.py          # writes wordnets_found.toml in-place
  uv run python scripts/sort_toml.py --check  # dry-run, print to stdout
"""

import argparse
import re
import sys
from pathlib import Path

ROOT     = Path(__file__).parent.parent
TOML_PATH = ROOT / "wordnets_found.toml"

FILE_HEADER = """\
# Wordnets Found
# Compiled from cygnet/wordnets.toml (known) + broad GitHub search.
# Entries sorted alphabetically by language, then by id.
#
# Fields: id, name, language, bcp47, format, confidence,
#         repo_url, release_url, example_file, license,
#         known_in_cygnet, notes, acl_ids, bib,
#         type, superseded-by
# format values: "GWA LMF", "OMW 1.0 tab", "Princeton WNDB", "RDF/TTL",
#                "VisDic XML", "DanNet TAB", "custom XML", "YAML", "GF",
#                "Lemon TTL", "alignment TSV", "JSON", "unknown"
# type values:   "expand", "standalone", "merge", "auto", "extension",
#                "alignment"
"""

SECTION_LINE = "# {bar} {lang} {bar2}"


def section_header(lang: str) -> str:
    total = 78
    inner = f" {lang} "
    dashes = "─" * max(0, (total - len(inner) - 4) // 2)
    return f"# {dashes}{inner}{dashes}"


def get_field(block: str, field: str) -> str | None:
    """Extract a simple string field from a raw TOML block."""
    m = re.search(rf'^{re.escape(field)}\s*=\s*"([^"]*)"', block, re.MULTILINE)
    return m.group(1) if m else None


def strip_section_comments(block: str) -> str:
    """Remove stale section-header comment lines from a block."""
    lines = block.split("\n")
    out = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("# ──") or stripped.startswith("# ══") or (
            stripped.startswith("#") and stripped.upper() == stripped and len(stripped) > 4
        ):
            continue
        out.append(line)
    # Remove leading blank lines after stripping comments
    while out and not out[0].strip():
        out.pop(0)
    # Collapse multiple consecutive blank lines to one
    result = []
    prev_blank = False
    for line in out:
        if not line.strip():
            if not prev_blank:
                result.append(line)
            prev_blank = True
        else:
            result.append(line)
            prev_blank = False
    return "\n".join(result)


def build_superseded_by_map(blocks: list[str]) -> dict[str, list[str]]:
    """
    Find all `supersedes = [...]` fields and return a mapping
    old_id → [new_id, ...] for the superseded-by direction.
    """
    result: dict[str, list[str]] = {}
    for block in blocks:
        m = re.search(r'^supersedes\s*=\s*\[([^\]]*)\]', block, re.MULTILINE)
        if not m:
            continue
        new_id = get_field(block, "id")
        old_ids = re.findall(r'"([^"]+)"', m.group(1))
        for old_id in old_ids:
            result.setdefault(old_id, []).append(new_id)
    return result


def transform_block(block: str, superseded_by: list[str] | None) -> str:
    """
    - Remove `supersedes = [...]` line.
    - Add `superseded-by = [...]` if provided.
    """
    # Remove supersedes line
    block = re.sub(r'^supersedes\s*=\s*\[[^\]]*\]\n?', '', block, flags=re.MULTILINE)
    # Remove existing superseded-by (idempotent)
    block = re.sub(r'^superseded-by\s*=\s*\[[^\]]*\]\n?', '', block, flags=re.MULTILINE)

    if superseded_by:
        ids_str = ", ".join(f'"{i}"' for i in superseded_by)
        new_line = f'superseded-by = [{ids_str}]\n'
        # Insert after `type = ...` line if present, else before bib/acl_ids/notes
        for anchor in (r'^type\s*=\s*"[^"]*"', r'^acl_ids\s*=', r'^bib\s*=', r'^notes\s*='):
            m = re.search(anchor, block, re.MULTILINE)
            if m:
                insert_at = m.end()
                # Move to end of that line
                nl = block.find('\n', insert_at)
                if nl == -1:
                    block = block + '\n' + new_line
                else:
                    block = block[:nl+1] + new_line + block[nl+1:]
                break
        else:
            block = block.rstrip('\n') + '\n' + new_line

    return block


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--check", action="store_true",
                   help="Print to stdout instead of writing in-place")
    args = p.parse_args(argv)

    content = TOML_PATH.read_text(encoding="utf-8")

    # Split into blocks
    parts = re.split(r'\n(?=\[\[wordnet\]\])', content)
    raw_blocks = parts[1:]  # skip file header

    # Build superseded-by map from existing supersedes fields
    superseded_by_map = build_superseded_by_map(raw_blocks)

    # Process each block
    processed = []
    for block in raw_blocks:
        block = strip_section_comments(block)
        wn_id = get_field(block, "id")
        block = transform_block(block, superseded_by_map.get(wn_id))
        lang = get_field(block, "language") or "Unknown"
        processed.append((lang.lower(), wn_id or "", block))

    # Sort by (language, id)
    processed.sort(key=lambda t: (t[0], t[1]))

    # Reconstruct file with language section headers
    out_parts = [FILE_HEADER]
    current_lang = None
    for lang_key, wn_id, block in processed:
        lang_display = get_field(block, "language") or "Unknown"
        if lang_display != current_lang:
            out_parts.append(f"\n{section_header(lang_display)}\n")
            current_lang = lang_display
        out_parts.append("\n" + block.strip('\n') + "\n")

    result = "\n".join(out_parts)

    if args.check:
        print(result)
    else:
        TOML_PATH.write_text(result, encoding="utf-8")
        print(f"Wrote {TOML_PATH} ({len(processed)} entries)")
        if superseded_by_map:
            print("superseded-by relationships added:")
            for old_id, new_ids in superseded_by_map.items():
                print(f"  {old_id} → superseded-by {new_ids}")


if __name__ == "__main__":
    sys.exit(main())
