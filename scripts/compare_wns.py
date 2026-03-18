#!/usr/bin/env python3
"""
compare_wns.py — Compare two (or more) wordnets by sense overlap.

For each pair, prints:
  - % of senses in A only
  - % of senses in B only
  - % in both  (all percentages over the union)

Sense identity:
  If the synset has an ILI:    key = (ili, lemma)
  Otherwise (no ili/ili=None): key = (synset_id, lemma)

So two senses match when they refer to the same ILI concept with the same
lemma — cross-wordnet comparison works even when synset IDs differ.  Senses
with no ILI are local to their wordnet and will never match a sense from
another wordnet.

Usage:
  uv run python scripts/compare_wns.py <id1> <id2> [id3 ...]
  uv run python scripts/compare_wns.py --language French
  uv run python scripts/compare_wns.py --all-languages   # pairs within each language
  uv run python scripts/compare_wns.py oewn odenet       # specific IDs

Output:
  Human-readable table.  Use --tsv for tab-separated output.
"""

import argparse
import sys
from pathlib import Path
from collections import defaultdict

ROOT = Path(__file__).parent.parent
PKG_DIR = ROOT / "build" / "pkg"
TOML_PATH = ROOT / "wordnets_found.toml"


# ── TOML helpers ─────────────────────────────────────────────────────────────

def _get_field(block: str, field: str):
    import re
    m = re.search(rf'^{re.escape(field)}\s*=\s*"([^"]*)"', block, re.MULTILINE)
    return m.group(1) if m else None


def load_catalogue():
    """Return list of dicts from wordnets_found.toml (id, language, bcp47, name)."""
    import re
    content = TOML_PATH.read_text(encoding="utf-8")
    parts = re.split(r'\n(?=\[\[wordnet\]\])', content)
    entries = []
    for block in parts[1:]:
        e = {
            "id":       _get_field(block, "id") or "?",
            "name":     _get_field(block, "name") or "",
            "language": _get_field(block, "language") or "",
            "bcp47":    _get_field(block, "bcp47") or "",
        }
        entries.append(e)
    return entries


# ── LMF loading ──────────────────────────────────────────────────────────────

def load_sense_keys(wn_id: str) -> tuple[set, dict]:
    """
    Load build/pkg/{wn_id}/{wn_id}.xml and return:
      (sense_keys, info_dict)

    sense_keys: set of (ili_or_synset_id, lemma) tuples
    info_dict:  {"n_entries": int, "n_synsets": int, "n_senses": int,
                 "n_with_ili": int, "n_without_ili": int}
    """
    import wn.lmf

    xml = PKG_DIR / wn_id / f"{wn_id}.xml"
    if not xml.exists():
        raise FileNotFoundError(f"No pkg file found: {xml}\n"
                                f"Run: uv run python scripts/download.py --ids {wn_id}")

    try:
        doc = wn.lmf.load(xml, progress_handler=None)
    except Exception as e:
        msg = str(e)
        if "DOCTYPE" in msg or "invalid" in msg.lower():
            raise FileNotFoundError(
                f"{wn_id}: not in GWA LMF format (wn.lmf cannot parse it).\n"
                f"  File: {xml}\n"
                f"  Error: {msg}"
            ) from e
        raise FileNotFoundError(f"Cannot load {xml}: {e}") from e

    keys = set()
    n_senses = 0
    n_with_ili = 0
    n_without_ili = 0
    n_entries = 0
    n_synsets = 0

    for lex in doc.get("lexicons", []):
        # Build synset_id → ili map
        ili_map: dict[str, str | None] = {}
        for ss in lex.get("synsets", []):
            n_synsets += 1
            ss_id = ss.get("id", "")
            ili = ss.get("ili") or None
            if ili in ("", "in", "in progress", None):
                ili = None
            ili_map[ss_id] = ili

        for entry in lex.get("entries", []):
            n_entries += 1
            lemma = (entry.get("lemma") or {}).get("writtenForm", "")
            for sense in entry.get("senses", []):
                n_senses += 1
                ss_id = sense.get("synset", "")
                ili = ili_map.get(ss_id)
                if ili:
                    n_with_ili += 1
                    keys.add((ili, lemma.lower()))
                else:
                    n_without_ili += 1
                    keys.add((f"synset:{ss_id}", lemma.lower()))

    info = {
        "n_entries":     n_entries,
        "n_synsets":     n_synsets,
        "n_senses":      n_senses,
        "n_with_ili":    n_with_ili,
        "n_without_ili": n_without_ili,
    }
    return keys, info


# ── Comparison ───────────────────────────────────────────────────────────────

def compare_pair(id_a: str, id_b: str) -> dict:
    """Compare two wordnets; return a result dict."""
    keys_a, info_a = load_sense_keys(id_a)
    keys_b, info_b = load_sense_keys(id_b)

    both  = keys_a & keys_b
    only_a = keys_a - keys_b
    only_b = keys_b - keys_a
    union = keys_a | keys_b

    n_union = len(union)
    n_both  = len(both)
    n_only_a = len(only_a)
    n_only_b = len(only_b)

    def pct(n):
        return (100.0 * n / n_union) if n_union else 0.0

    # Jaccard similarity
    jaccard = (n_both / n_union) if n_union else 0.0

    # Is B a subset/superset of A?
    a_covers_b = (len(keys_b) > 0) and (len(only_b) == 0)
    b_covers_a = (len(keys_a) > 0) and (len(only_a) == 0)

    return {
        "id_a": id_a, "id_b": id_b,
        "info_a": info_a, "info_b": info_b,
        "n_a": len(keys_a), "n_b": len(keys_b),
        "n_both": n_both, "n_only_a": n_only_a, "n_only_b": n_only_b,
        "n_union": n_union,
        "pct_both":   pct(n_both),
        "pct_only_a": pct(n_only_a),
        "pct_only_b": pct(n_only_b),
        "jaccard": jaccard,
        "a_covers_b": a_covers_b,
        "b_covers_a": b_covers_a,
    }


def suggest_relationship(r: dict) -> str:
    """Rough heuristic label for human review."""
    j = r["jaccard"]
    if r["a_covers_b"]:
        return f"B ⊆ A  ({r['id_a']} supersedes?)"
    if r["b_covers_a"]:
        return f"A ⊆ B  ({r['id_b']} supersedes?)"
    if j >= 0.90:
        return "near-identical — pick one"
    if j >= 0.50:
        return "large overlap — likely superseded"
    if j >= 0.10:
        return "partial overlap — complementary or related versions"
    return "mostly disjoint — complementary"


# ── Output ───────────────────────────────────────────────────────────────────

def print_report(results: list[dict], tsv: bool = False):
    if tsv:
        header = "\t".join([
            "id_a", "senses_a", "ili_a",
            "id_b", "senses_b", "ili_b",
            "n_union", "n_both", "n_only_a", "n_only_b",
            "pct_both", "pct_only_a", "pct_only_b",
            "jaccard", "suggestion",
        ])
        print(header)
        for r in results:
            row = "\t".join([
                r["id_a"], str(r["n_a"]), str(r["info_a"]["n_with_ili"]),
                r["id_b"], str(r["n_b"]), str(r["info_b"]["n_with_ili"]),
                str(r["n_union"]), str(r["n_both"]),
                str(r["n_only_a"]), str(r["n_only_b"]),
                f"{r['pct_both']:.1f}", f"{r['pct_only_a']:.1f}", f"{r['pct_only_b']:.1f}",
                f"{r['jaccard']:.3f}",
                suggest_relationship(r),
            ])
            print(row)
        return

    for r in results:
        ia, ib = r["info_a"], r["info_b"]
        print(f"\n{'═'*68}")
        print(f"  {r['id_a']}  vs  {r['id_b']}")
        print(f"{'─'*68}")
        print(f"  {r['id_a']:30s}  {r['n_a']:>7,} senses  "
              f"({ia['n_with_ili']:,} with ILI, {ia['n_without_ili']:,} without)")
        print(f"  {r['id_b']:30s}  {r['n_b']:>7,} senses  "
              f"({ib['n_with_ili']:,} with ILI, {ib['n_without_ili']:,} without)")
        print()
        print(f"  Union of senses:   {r['n_union']:>7,}")
        print(f"  In both:           {r['n_both']:>7,}  ({r['pct_both']:5.1f}%)")
        print(f"  Only in {r['id_a']:20s}  {r['n_only_a']:>7,}  ({r['pct_only_a']:5.1f}%)")
        print(f"  Only in {r['id_b']:20s}  {r['n_only_b']:>7,}  ({r['pct_only_b']:5.1f}%)")
        print()
        print(f"  Jaccard similarity: {r['jaccard']:.3f}")
        print(f"  Suggestion:  {suggest_relationship(r)}")
    print(f"\n{'═'*68}")


# ── Main ─────────────────────────────────────────────────────────────────────

def main(argv=None):
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("ids", nargs="*", metavar="ID",
                   help="Wordnet IDs to compare (pairwise)")
    p.add_argument("--language", "-l", metavar="LANG",
                   help="Compare all catalogued wordnets for a language")
    p.add_argument("--bcp47", metavar="TAG",
                   help="Compare all wordnets with this BCP-47 tag")
    p.add_argument("--all-languages", action="store_true",
                   help="Compare pairs within each language (slow)")
    p.add_argument("--tsv", action="store_true",
                   help="Tab-separated output")
    args = p.parse_args(argv)

    ids_to_compare: list[str] = list(args.ids)

    if args.language or args.bcp47 or args.all_languages:
        catalogue = load_catalogue()
        if args.language:
            ids_to_compare = [
                e["id"] for e in catalogue
                if args.language.lower() in e["language"].lower()
            ]
            if not ids_to_compare:
                print(f"No wordnets found for language: {args.language}", file=sys.stderr)
                return 1
        elif args.bcp47:
            ids_to_compare = [
                e["id"] for e in catalogue
                if e["bcp47"].lower() == args.bcp47.lower()
            ]
            if not ids_to_compare:
                print(f"No wordnets found for BCP-47: {args.bcp47}", file=sys.stderr)
                return 1
        elif args.all_languages:
            by_lang: dict[str, list[str]] = defaultdict(list)
            for e in catalogue:
                by_lang[e["language"]].append(e["id"])
            ids_to_compare = []  # handled below
            all_pairs = []
            for lang, wn_ids in sorted(by_lang.items()):
                if len(wn_ids) >= 2:
                    for i, a in enumerate(wn_ids):
                        for b in wn_ids[i+1:]:
                            all_pairs.append((a, b, lang))
            results = []
            for a, b, lang in all_pairs:
                try:
                    r = compare_pair(a, b)
                    r["language"] = lang
                    results.append(r)
                except FileNotFoundError as e:
                    print(f"SKIP {a} vs {b}: {e}", file=sys.stderr)
            print_report(results, tsv=args.tsv)
            return 0

    if len(ids_to_compare) < 2:
        p.error("Provide at least two wordnet IDs (or use --language / --bcp47)")

    # Generate all pairs
    pairs = []
    for i, a in enumerate(ids_to_compare):
        for b in ids_to_compare[i+1:]:
            pairs.append((a, b))

    results = []
    for a, b in pairs:
        try:
            results.append(compare_pair(a, b))
        except FileNotFoundError as e:
            print(f"SKIP {a} vs {b}: {e}", file=sys.stderr)
            continue

    print_report(results, tsv=args.tsv)
    return 0


if __name__ == "__main__":
    sys.exit(main())
