#!/usr/bin/env python3
"""
One-shot migration: inject acl_ids / bib fields from make_citations.py
into wordnets_found.toml.  Run once, then delete this script.
"""
import importlib.util
import re
import sys
import types
from pathlib import Path

ROOT = Path(__file__).parent.parent

# ── Load ACL_IDS / MANUAL_BIB / TUFS_BIB from make_citations ──────────────
mc_path = ROOT / "scripts" / "make_citations.py"
mc_src  = mc_path.read_text()
# Provide __file__ so the module-level ROOT assignment works
ns: dict = {"__file__": str(mc_path)}
exec(compile(mc_src, str(mc_path), "exec"), ns)  # noqa: S102

ACL_IDS    = ns["ACL_IDS"]
MANUAL_BIB = ns["MANUAL_BIB"]

# ── Read TOML as raw text ──────────────────────────────────────────────────
toml_path = ROOT / "wordnets_found.toml"
text = toml_path.read_text()

# Split on [[wordnet]] boundaries
BLOCK_RE = re.compile(r'(\[\[wordnet\]\]\n)', re.MULTILINE)
parts = BLOCK_RE.split(text)
preamble = parts[0]
blocks = []
for i in range(1, len(parts), 2):
    header = parts[i]
    body   = parts[i + 1] if i + 1 < len(parts) else ""
    blocks.append([header, body])


def get_id(body: str) -> str | None:
    m = re.search(r'^id\s*=\s*"([^"]+)"', body, re.MULTILINE)
    return m.group(1) if m else None


def has_field(body: str, field: str) -> bool:
    return bool(re.search(rf'^{re.escape(field)}\s*=', body, re.MULTILINE))


def toml_multiline(s: str) -> str:
    s = s.strip()
    # Use TOML literal multi-line strings (''' ... ''')
    # If the string itself contains ''', fall back to basic multiline
    if "'''" in s:
        s = s.replace('"', '\\"')
        return f'"""\n{s}\n"""'
    return f"'''\n{s}\n'''"


changed = 0
for pair in blocks:
    header, body = pair
    wn_id = get_id(body)
    if not wn_id:
        continue

    additions = []

    # acl_ids
    if not has_field(body, "acl_ids"):
        if wn_id in ACL_IDS:
            ids_toml = "[" + ", ".join(f'"{x}"' for x in ACL_IDS[wn_id]) + "]"
            additions.append(f'acl_ids = {ids_toml}')
        elif wn_id.startswith("tufs-"):
            additions.append('acl_ids = ["2020.lrec-1.389"]')

    # bib
    if wn_id in MANUAL_BIB and not has_field(body, "bib"):
        additions.append(f'bib = {toml_multiline(MANUAL_BIB[wn_id])}')

    if additions:
        stripped  = body.rstrip('\n')
        trailing  = body[len(stripped):]
        pair[1]   = stripped + "\n" + "\n".join(additions) + "\n" + trailing
        changed  += 1

new_text = preamble + "".join(h + b for h, b in blocks)
toml_path.write_text(new_text)
print(f"Injected citation fields into {changed} entries.")
