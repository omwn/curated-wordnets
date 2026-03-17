#!/usr/bin/env python3
"""
add_type_fields.py — Insert `type` (and optionally `supersedes`) fields
into every [[wordnet]] block in wordnets_found.toml.

Fields are inserted after the last existing field in each block, using
text-based block manipulation (no TOML round-trip rewrite).
"""

import re
import sys
from pathlib import Path

TOML_PATH = Path(__file__).parent.parent / "wordnets_found.toml"

# ---------------------------------------------------------------------------
# Classification table
# Keys are wordnet `id` values.  Values are dicts with:
#   type        : str  (required)
#   supersedes  : list[str]  (optional; omit key entirely if empty)
# ---------------------------------------------------------------------------

CLASSIFICATIONS: dict[str, dict] = {
    # ── ENGLISH ──────────────────────────────────────────────────────────
    "oewn":           {"type": "standalone"},
    "oewn-namenet":   {"type": "extension"},

    # ── ARABIC ───────────────────────────────────────────────────────────
    "omw-arb":        {"type": "expand"},
    "awn4":           {"type": "expand"},
    "tufs-arb":       {"type": "expand"},

    # ── BULGARIAN ────────────────────────────────────────────────────────
    "omw-bg":         {"type": "expand"},

    # ── CATALAN ──────────────────────────────────────────────────────────
    "omw-ca":         {"type": "expand"},

    # ── CHINESE (MANDARIN) ───────────────────────────────────────────────
    "omw-cmn":        {"type": "expand"},
    "cow":            {"type": "expand"},
    "tufs-cmn":       {"type": "expand"},

    # ── DANISH ───────────────────────────────────────────────────────────
    "omw-da":         {"type": "expand"},
    "dannet":         {"type": "standalone"},

    # ── GERMAN ───────────────────────────────────────────────────────────
    "odenet":         {"type": "merge"},
    "tufs-de":        {"type": "expand"},

    # ── GREEK ────────────────────────────────────────────────────────────
    "omw-el":         {"type": "expand"},

    # ── SPANISH ──────────────────────────────────────────────────────────
    "omw-es":         {"type": "expand"},
    "tufs-es":        {"type": "expand"},

    # ── BASQUE ───────────────────────────────────────────────────────────
    "omw-eu":         {"type": "expand"},

    # ── FINNISH ──────────────────────────────────────────────────────────
    "omw-fi":         {"type": "expand"},

    # ── FRENCH ───────────────────────────────────────────────────────────
    "omw-fr":         {"type": "expand"},
    "tufs-fr":        {"type": "expand"},

    # ── GALICIAN ─────────────────────────────────────────────────────────
    "omw-gl":         {"type": "expand"},
    "galnet":         {"type": "expand"},

    # ── HEBREW ───────────────────────────────────────────────────────────
    "omw-he":         {"type": "expand"},

    # ── CROATIAN ─────────────────────────────────────────────────────────
    "omw-hr":         {"type": "expand"},

    # ── ICELANDIC ────────────────────────────────────────────────────────
    "omw-is":         {"type": "expand"},

    # ── ITALIAN ──────────────────────────────────────────────────────────
    "omw-it":         {"type": "expand"},
    "omw-iwn":        {"type": "expand"},
    "iwn-omw":        {"type": "expand", "supersedes": ["omw-iwn"]},

    # ── JAPANESE ─────────────────────────────────────────────────────────
    "omw-ja":         {"type": "expand"},
    "tufs-ja":        {"type": "expand"},
    "wnja":           {"type": "expand"},

    # ── MALAY ────────────────────────────────────────────────────────────
    "omw-zsm":        {"type": "expand"},
    "tufs-zsm":       {"type": "expand"},

    # ── INDONESIAN ───────────────────────────────────────────────────────
    "omw-id":         {"type": "expand"},
    "tufs-id":        {"type": "expand"},

    # ── KURDISH ──────────────────────────────────────────────────────────
    "kurdnet":        {"type": "expand"},

    # ── LITHUANIAN ───────────────────────────────────────────────────────
    "omw-lt":         {"type": "expand"},

    # ── NORWEGIAN ────────────────────────────────────────────────────────
    "omw-nb":         {"type": "expand"},
    "omw-nn":         {"type": "expand"},
    "norsk-ordvev-nb":{"type": "standalone"},
    "norsk-ordvev-nn":{"type": "standalone"},

    # ── DUTCH ────────────────────────────────────────────────────────────
    "omw-nl":         {"type": "expand"},
    "odwn":           {"type": "merge"},
    "odwn-lmf":       {"type": "merge", "supersedes": ["odwn"]},

    # ── POLISH ───────────────────────────────────────────────────────────
    "omw-pl":         {"type": "expand"},
    "plwordnet-gw":   {"type": "standalone"},

    # ── PORTUGUESE ───────────────────────────────────────────────────────
    "own-pt":         {"type": "expand"},
    "tufs-pt":        {"type": "expand"},

    # ── ROMANIAN ─────────────────────────────────────────────────────────
    "omw-ro":         {"type": "expand"},
    "rowordnet":      {"type": "standalone"},

    # ── SLOVAK ───────────────────────────────────────────────────────────
    "omw-sk":         {"type": "expand"},

    # ── SLOVENIAN ────────────────────────────────────────────────────────
    "omw-sl":         {"type": "expand"},

    # ── ALBANIAN ─────────────────────────────────────────────────────────
    "omw-sq":         {"type": "expand"},

    # ── SWEDISH ──────────────────────────────────────────────────────────
    "omw-sv":         {"type": "expand"},

    # ── THAI ─────────────────────────────────────────────────────────────
    "omw-th":         {"type": "expand"},
    "tufs-th":        {"type": "expand"},

    # ── LATVIAN ──────────────────────────────────────────────────────────
    "tezaurs":        {"type": "standalone"},

    # ── ASSAMESE ─────────────────────────────────────────────────────────
    "tufs-as":        {"type": "expand"},

    # ── KHMER ────────────────────────────────────────────────────────────
    "tufs-km":        {"type": "expand"},

    # ── KOREAN ───────────────────────────────────────────────────────────
    "tufs-ko":        {"type": "expand"},

    # ── LAO ──────────────────────────────────────────────────────────────
    "tufs-lo":        {"type": "expand"},

    # ── MONGOLIAN ────────────────────────────────────────────────────────
    "tufs-mn":        {"type": "expand"},

    # ── BURMESE ──────────────────────────────────────────────────────────
    "tufs-my":        {"type": "expand"},
    "mow":            {"type": "expand"},

    # ── RUSSIAN ──────────────────────────────────────────────────────────
    "tufs-ru":        {"type": "expand"},

    # ── FILIPINO ─────────────────────────────────────────────────────────
    "tufs-tl":        {"type": "expand"},

    # ── TURKISH ──────────────────────────────────────────────────────────
    "tufs-tr":        {"type": "expand"},
    "kenet":          {"type": "standalone"},

    # ── URDU ─────────────────────────────────────────────────────────────
    "tufs-ur":        {"type": "expand"},

    # ── VIETNAMESE ───────────────────────────────────────────────────────
    "tufs-vi":        {"type": "expand"},
    "viwn":           {"type": "expand"},

    # ── CANTONESE ────────────────────────────────────────────────────────
    "cantonese-wn":   {"type": "expand"},

    # ── ABUI ─────────────────────────────────────────────────────────────
    "abuiwn":         {"type": "expand"},

    # ── OLD JAVANESE ─────────────────────────────────────────────────────
    "ojw":            {"type": "expand"},

    # ── ADDITIONAL (not in cygnet) ────────────────────────────────────────
    "wordnet-gaeilge":{"type": "expand"},
    "wn-data-gle":    {"type": "expand"},
    "gawn-lemon":     {"type": "expand"},

    "uzwordnet":      {"type": "expand"},

    "open-afrikaans-wn": {"type": "expand"},
    "ua-wordnet":     {"type": "expand"},
    "open-xhosa-wn":  {"type": "expand"},
    "open-zulu-wn":   {"type": "expand"},
    "open-sesotho-wn":{"type": "expand"},
    "open-tsonga-wn": {"type": "expand"},

    "huwn":           {"type": "expand"},

    # ── TAMIL ────────────────────────────────────────────────────────────
    "twn":            {"type": "expand"},

    # ── GF WORDNET ───────────────────────────────────────────────────────
    "gf-wordnet":     {"type": "auto"},

    # ── PUNJABI ──────────────────────────────────────────────────────────
    "tufs-pb":        {"type": "expand"},

    # ── SINHALA ──────────────────────────────────────────────────────────
    "sinhala-wn":     {"type": "expand"},

    # ── SERBIAN ──────────────────────────────────────────────────────────
    "srpwn":          {"type": "expand"},   # first stub entry (GitHub)

    # ── VIETNAMESE additional ─────────────────────────────────────────────
    "viwn-build":     {"type": "expand"},

    # ── ADDITIONAL FINDS ─────────────────────────────────────────────────
    "monwn":          {"type": "expand"},
    "wncy":           {"type": "expand"},
    "african-wn-sefara": {"type": "expand"},

    "latinwn-revision":  {"type": "expand"},
    "latinwn-archive":   {"type": "expand"},

    # ── INDIAN / ALIGNMENT ───────────────────────────────────────────────
    "iwn-en":         {"type": "alignment"},

    # ── CLARIN REPOSITORIES ──────────────────────────────────────────────
    "estwn":          {"type": "expand"},
    "icelandic-wordweb": {"type": "standalone"},
    "agwn":           {"type": "expand"},
    "italwordnet-v2": {"type": "expand"},
    "slownet":        {"type": "expand"},
    "oswn":           {"type": "standalone"},
    "finnwordnet":    {"type": "standalone"},
    "plwordnet-4":    {"type": "standalone"},
    "czech-wn":       {"type": "expand"},
    "euswn":          {"type": "expand"},

    # ── ADDITIONAL (CLARIN / other repos) ────────────────────────────────
    "btb-wordnet":    {"type": "expand"},
    "bulnet":         {"type": "expand"},
    # second srpwn entry (official CC BY-NC)
    # NB: both entries share the same id "srpwn"; handled below

    "mwnpt":          {"type": "expand"},

    # ── OMW v1 LIST + OTHER ───────────────────────────────────────────────
    "cwn-taiwan":     {"type": "standalone"},
    "farsnet":        {"type": "expand"},
    "wolf":           {"type": "auto"},
    "greek-wn-okfngr":{"type": "expand"},
    "albanet":        {"type": "expand"},
    "aeb-wn":         {"type": "expand"},
    "sardanet":       {"type": "standalone"},
    "sanskrit-wn":    {"type": "standalone"},
    "coptic-wn":      {"type": "standalone"},
}

# ---------------------------------------------------------------------------
# Helper: build the TOML lines to insert for a given id
# ---------------------------------------------------------------------------

def build_insertion(wn_id: str) -> str:
    info = CLASSIFICATIONS.get(wn_id)
    if info is None:
        return ""
    lines = [f'type = "{info["type"]}"']
    if "supersedes" in info:
        ids_toml = ", ".join(f'"{x}"' for x in info["supersedes"])
        lines.append(f"supersedes = [{ids_toml}]")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Block-level text manipulation
# ---------------------------------------------------------------------------

def process(text: str) -> str:
    """
    Split the file into [[wordnet]] blocks and non-block segments,
    insert type/supersedes fields into each block, then reassemble.
    """
    # Split on [[wordnet]] markers, keeping the delimiter
    parts = re.split(r'(\[\[wordnet\]\])', text)
    # parts: [preamble, '[[wordnet]]', block_body, '[[wordnet]]', block_body, ...]

    result_parts = [parts[0]]  # preamble (before first [[wordnet]])

    i = 1
    while i < len(parts):
        header = parts[i]       # '[[wordnet]]'
        body   = parts[i + 1] if i + 1 < len(parts) else ""
        i += 2

        # Find the wordnet id in the body
        id_match = re.search(r'^id\s*=\s*"([^"]+)"', body, re.MULTILINE)
        if id_match is None:
            result_parts.append(header)
            result_parts.append(body)
            continue

        wn_id = id_match.group(1)
        insertion = build_insertion(wn_id)

        if not insertion:
            # No classification found — leave block unchanged
            print(f"WARNING: no classification for id={wn_id!r}", file=sys.stderr)
            result_parts.append(header)
            result_parts.append(body)
            continue

        # Check if type already exists in this block
        if re.search(r'^type\s*=', body, re.MULTILINE):
            # Already has type — skip
            result_parts.append(header)
            result_parts.append(body)
            continue

        # The block body ends either at the next [[wordnet]] (already split off)
        # or at the end of the file.  We want to insert AFTER the last "plain"
        # field line (i.e., before any trailing blank lines / bib block).
        #
        # Strategy: find the last non-blank, non-comment line in the block that
        # isn't a bib multiline string, then insert after it.
        #
        # The block body may contain a multiline bib = ''' ... ''' section.
        # We insert BEFORE the bib section (or at the end of the last field).

        # Find where the bib/acl_ids section starts (if any), or end of block.
        # We insert right after the last key=value line of the "header" fields,
        # before any free-standing bib/acl_ids lines.
        #
        # Approach: find the last occurrence of a simple key = value / key = ["..."] line
        # that is NOT inside a ''' ... ''' multiline string.

        # Remove multiline bib strings to find the "real" last field line.
        body_no_bib = re.sub(r"bib\s*=\s*'''.+?'''", "", body, flags=re.DOTALL)

        # Find the last key = ... line (TOML field)
        field_lines = list(re.finditer(r'^[a-zA-Z_][a-zA-Z0-9_]*\s*=\s*.+', body_no_bib, re.MULTILINE))

        if not field_lines:
            # Fallback: just prepend at start of body
            new_body = "\n" + insertion + "\n" + body
        else:
            last_field = field_lines[-1]
            insert_pos = last_field.end()
            new_body = body[:insert_pos] + "\n" + insertion + body[insert_pos:]

        result_parts.append(header)
        result_parts.append(new_body)

    return "".join(result_parts)


def main():
    text = TOML_PATH.read_text(encoding="utf-8")
    new_text = process(text)
    TOML_PATH.write_text(new_text, encoding="utf-8")
    print(f"Done. Written to {TOML_PATH}")


if __name__ == "__main__":
    main()
