#!/usr/bin/env python3
"""
summary.py — Print statistics and a Markdown table for all wordnets.

Usage:
  uv run python summary.py              # full table + stats
  uv run python summary.py --stats      # stats only
  uv run python summary.py --md > README_table.md
"""

import argparse
import json
import sys
from pathlib import Path

import tomllib

ROOT = Path(__file__).parent.parent
TOML_PATH = ROOT / "wordnets_found.toml"
RESULTS_PATH = ROOT / "build" / "results.json"


# ── helpers ────────────────────────────────────────────────────────────────

def load_results() -> dict:
    if RESULTS_PATH.exists():
        return json.loads(RESULTS_PATH.read_text())
    return {}


def status_label(wn_id: str, results: dict) -> str:
    r = results.get(wn_id, {})
    dl = r.get("download")
    val = r.get("validation")
    if not dl:
        return "not tried"
    if dl == "failed":
        return "dl failed"
    if dl == "ok":
        if val == "ok":
            wc = r.get("warning_count", 0)
            return f"✓ ok ({wc}W)" if wc else "✓ ok"
        if val == "errors":
            ec = r.get("error_count", 0)
            return f"✗ errors ({ec}E)"
        if val == "parse_error":
            return "✗ parse error"
        if val == "skipped":
            note = r.get("validation_note", "")[:40]
            return f"skipped ({note})" if note else "skipped"
        return f"dl ok / {val or '?'}"
    return dl


def licence_short(lic: str | None) -> str:
    if not lic:
        return "?"
    for tok in ("CC BY-SA 4.0", "CC BY-SA", "CC BY 4.0", "CC BY 3.0", "CC BY",
                "CC BY-NC", "wordnet", "MIT", "Apache", "GPL"):
        if tok.lower() in lic.lower():
            return tok
    return lic[:30]


# ── main ───────────────────────────────────────────────────────────────────

def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--stats", action="store_true", help="Print stats only, no table")
    p.add_argument("--md", action="store_true", help="Output Markdown (default: also print stats)")
    args = p.parse_args(argv)

    with open(TOML_PATH, "rb") as f:
        data = tomllib.load(f)
    entries = data.get("wordnet", [])
    results = load_results()

    # ── stats ──────────────────────────────────────────────────────────────
    total = len(entries)
    tried = sum(1 for e in entries if e["id"] in results)
    dl_ok = sum(1 for e in entries
                if results.get(e["id"], {}).get("download") == "ok")
    val_ok = sum(1 for e in entries
                 if results.get(e["id"], {}).get("validation") == "ok")
    val_warn = sum(1 for e in entries
                   if results.get(e["id"], {}).get("validation") == "ok"
                   and results.get(e["id"], {}).get("warning_count", 0) > 0)
    val_err = sum(1 for e in entries
                  if results.get(e["id"], {}).get("validation") == "errors")
    parse_err = sum(1 for e in entries
                    if results.get(e["id"], {}).get("validation") == "parse_error")
    dl_fail = sum(1 for e in entries
                  if results.get(e["id"], {}).get("download") == "failed")
    skipped = sum(1 for e in entries
                  if results.get(e["id"], {}).get("validation") == "skipped")
    not_tried = total - tried

    # confidence breakdown
    from collections import Counter
    conf = Counter(e.get("confidence", "?") for e in entries)
    fmt  = Counter(e.get("format", "unknown") for e in entries)

    print(f"## Wordnet Summary\n")
    print(f"| Metric | Count |")
    print(f"|--------|-------|")
    print(f"| Total entries in TOML | {total} |")
    print(f"| High confidence | {conf.get('high', 0)} |")
    print(f"| Medium confidence | {conf.get('medium', 0)} |")
    print(f"| Low confidence | {conf.get('low', 0)} |")
    print(f"| Download attempted | {tried} |")
    print(f"| Download OK | {dl_ok} |")
    print(f"| Download failed | {dl_fail} |")
    print(f"| Not yet tried | {not_tried} |")
    print(f"| **Validated OK** | **{val_ok}** |")
    print(f"| — of which with warnings | {val_warn} |")
    print(f"| — of which fully clean | {val_ok - val_warn} |")
    print(f"| Validation errors | {val_err} |")
    print(f"| XML parse errors | {parse_err} |")
    print(f"| Skipped (non-LMF) | {skipped} |")
    print()

    print(f"### Formats\n")
    print(f"| Format | Count |")
    print(f"|--------|-------|")
    for f, n in fmt.most_common():
        print(f"| {f} | {n} |")
    print()

    if args.stats:
        return 0

    # ── table ──────────────────────────────────────────────────────────────
    cols = ["ID", "Name", "Language", "BCP-47", "Format", "License", "Confidence", "Status"]
    rows = []
    for e in entries:
        wn_id   = e["id"]
        name    = e.get("name", wn_id)
        lang    = e.get("language", "")
        bcp     = e.get("bcp47", "")
        fmt_val = e.get("format", "unknown")
        lic     = licence_short(e.get("license"))
        conf_v  = e.get("confidence", "?")
        status  = status_label(wn_id, results)
        rows.append([wn_id, name, lang, bcp, fmt_val, lic, conf_v, status])

    # column widths
    widths = [max(len(cols[i]), max(len(r[i]) for r in rows)) for i in range(len(cols))]

    def fmt_row(r):
        return "| " + " | ".join(str(r[i]).ljust(widths[i]) for i in range(len(cols))) + " |"

    print("### Full Wordnet Table\n")
    print(fmt_row(cols))
    print("|" + "|".join("-" * (w + 2) for w in widths) + "|")
    for r in rows:
        print(fmt_row(r))
    print()

    return 0


if __name__ == "__main__":
    sys.exit(main())
