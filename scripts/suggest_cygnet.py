#!/usr/bin/env python3
"""
suggest_cygnet.py — Propose additions and updates to cygnet's wordnets.toml.

Compares our curated-wordnets catalogue and build results against the current
cygnet wordnets.toml, then produces:

  build/cygnet/wordnets.toml        — proposed new cygnet file
  build/cygnet/CHANGES.md           — documented changes and rationale
  build/cygnet/close_to_adoption.md — wordnets nearly ready, what's blocking them

A wordnet is proposed for addition/update if:
  - download succeeded and it has a GWA LMF XML (even if it required cosmetic
    normalisation like DTD upgrade or stub synset patching)
  - the source URL did not require format conversion (no converted_from)
  - cygnet is robust and will discard invalid entries, so validation errors
    are noted but do not block inclusion

The close-to-adoption list covers wordnets we know about but cannot directly
put into cygnet:
  - Wordnets we had to convert (needs upstream GWA LMF release)
  - Wordnets with broken download URLs
  - Wordnets whose XML fails to parse

Usage:
  uv run python scripts/suggest_cygnet.py
  uv run python scripts/suggest_cygnet.py --cygnet-dir /path/to/cygnet/clone
  uv run python scripts/suggest_cygnet.py --test
"""

import argparse
import json
import re
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

try:
    import tomllib
except ImportError:
    import tomli as tomllib  # type: ignore[import-not-found]

import requests

ROOT = Path(__file__).parent.parent
RESULTS_PATH = ROOT / "build" / "results.json"
TOML_PATH = ROOT / "wordnets_found.toml"
OUT_DIR = ROOT / "build" / "cygnet"
CYGNET_RAW_URL = "https://raw.githubusercontent.com/omwn/cygnet/main/wordnets.toml"
CYGNET_REPO_URL = "https://github.com/omwn/cygnet.git"


# ── data loading ──────────────────────────────────────────────────────────────

def load_results() -> dict:
    if RESULTS_PATH.exists():
        return json.loads(RESULTS_PATH.read_text())
    return {}


def load_toml_entries() -> list[dict]:
    with open(TOML_PATH, "rb") as f:
        data = tomllib.load(f)
    return data.get("wordnet", [])


def fetch_cygnet_toml(cygnet_dir: Path | None) -> str:
    if cygnet_dir and (cygnet_dir / "wordnets.toml").exists():
        return (cygnet_dir / "wordnets.toml").read_text()
    print("Fetching cygnet/wordnets.toml from GitHub …")
    r = requests.get(CYGNET_RAW_URL, timeout=30)
    r.raise_for_status()
    return r.text


def parse_cygnet_toml(text: str) -> dict[str, list[str]]:
    """Parse cygnet's flat {bcp47: [url, ...]} TOML."""
    try:
        data = tomllib.loads(text)
        return {k: v for k, v in data.items() if isinstance(v, list)}
    except Exception:
        result: dict[str, list[str]] = {}
        for m in re.finditer(r'^(\w[\w-]*)\s*=\s*\[([^\]]*)\]', text, re.MULTILINE | re.DOTALL):
            urls = re.findall(r'"([^"]+)"', m.group(2))
            if urls:
                result[m.group(1)] = urls
        return result


# ── URL helpers ───────────────────────────────────────────────────────────────

def gh_release_parts(url: str) -> tuple[str, str, str] | None:
    """Return (owner/repo, version_tag, filename_stem) for a GitHub release URL."""
    m = re.match(
        r'https://github\.com/([^/]+/[^/]+)/releases/download/([^/]+)/(.+)', url
    )
    if not m:
        return None
    repo = m.group(1).lower()
    tag = m.group(2)
    filename = m.group(3)
    # Strip extension(s): .tar.xz .tar.gz .zip .xml.gz .xml
    stem = re.sub(r'\.(tar\.(xz|gz|bz2)|zip|xml\.gz|xml)$', '', filename, flags=re.I)
    # Strip trailing version (-2.0, -v1.4, -2026.04.06)
    stem = re.sub(r'[-_]v?' + re.escape(tag.lstrip('v')) + r'$', '', stem)
    stem = re.sub(r'[-_]v?\d+[\d\.\-]*$', '', stem)
    return repo, tag, stem.lower()


def url_fingerprint(url: str) -> str:
    """Stable identifier for a URL with version stripped — for matching."""
    parts = gh_release_parts(url)
    if parts:
        repo, _, stem = parts
        return f"gh:{repo}:{stem}"
    # Non-GitHub: strip date/version patterns
    base = re.sub(r'[-_]v?\d{4}[-\.\d]*', '', url)
    base = re.sub(r'[-_]v?\d+[\.\d]+', '', base)
    return base.lower()


def url_version(url: str) -> str:
    """Extract version string from a URL."""
    parts = gh_release_parts(url)
    if parts:
        return parts[1]
    # Date embedded in filename
    m = re.search(r'[-_](\d{4}[-\.]\d{2}[-\.]\d{2})', url)
    if m:
        return m.group(1)
    m = re.search(r'[-_]v?(\d+\.\d+[\.\d]*)', url)
    if m:
        return m.group(1)
    return ""


def version_tuple(v: str) -> tuple[int, ...]:
    v = v.lstrip('v')
    parts = re.split(r'[-\.]', v)
    result = []
    for p in parts:
        try:
            result.append(int(p))
        except ValueError:
            pass
    return tuple(result) if result else (0,)


def is_strictly_newer(ours: str, theirs: str) -> bool:
    return version_tuple(ours) > version_tuple(theirs)


# ── cygnet key mapping ────────────────────────────────────────────────────────

def cygnet_key(bcp47: str) -> str:
    """Map our BCP-47 code to the cygnet key convention."""
    # pt-BR merges into pt
    if bcp47 == "pt-BR":
        return "pt"
    return bcp47


# ── matching ──────────────────────────────────────────────────────────────────

def build_fingerprint_index(cygnet: dict[str, list[str]]) -> dict[str, tuple[str, str]]:
    """
    Build: fingerprint -> (bcp47, current_url)
    Used to find whether a URL from our catalogue already exists in cygnet.
    """
    idx: dict[str, tuple[str, str]] = {}
    for bcp47, urls in cygnet.items():
        for url in urls:
            idx[url_fingerprint(url)] = (bcp47, url)
    return idx


def best_url(entry: dict) -> str | None:
    """The URL we'd put into cygnet for this entry."""
    return entry.get("release_url") or entry.get("example_file") or None


# ── classification ────────────────────────────────────────────────────────────

def is_cygnet_eligible(entry: dict, result: dict) -> bool:
    """
    True if this wordnet can be added/updated in cygnet directly:
    - Successfully downloaded and has a GWA LMF XML
    - Source format was already GWA LMF (no format conversion required)
    - XML parses (validation errors are OK; parse_error means wn can't load it at all)
    - Has a usable URL
    """
    if result.get("download") != "ok":
        return False
    if not result.get("xml"):
        return False
    if result.get("converted_from"):
        return False
    if result.get("validation") == "parse_error":
        return False
    if not best_url(entry):
        return False
    return True


def close_to_adoption_reason(entry: dict, result: dict) -> str | None:
    """
    Return a reason string if this wordnet belongs on the close-to-adoption
    list, or None if it's either already eligible or too far away.
    """
    dl = result.get("download", "")
    cf = result.get("converted_from", "")
    val = result.get("validation", "")
    fmt = entry.get("format", "")

    if cf and result.get("xml"):
        return (
            f"Converted from {cf} — validates {'OK' if val == 'ok' else 'with issues'}; "
            f"needs upstream GWA LMF release URL"
        )

    if dl == "ok" and result.get("xml") and val == "parse_error":
        note = result.get("validation_note", "")[:80]
        return f"GWA LMF XML doesn't parse: {note}"

    if dl == "failed" and fmt == "GWA LMF":
        return "Download URL broken — needs a new release URL upstream"

    return None


# ── proposed TOML generation ──────────────────────────────────────────────────

def write_proposed_toml(
    proposed: dict[str, list[str]],
    comments: dict[str, str],
) -> str:
    lines = [
        "# Wordnets to include in Cygnet.",
        "# Keys are BCP 47 / ISO 639 language codes; values are lists of source archive URLs.",
        "# To add a language, append an entry here and re-run build.sh.",
        "",
    ]
    for key in sorted(proposed.keys()):
        urls = proposed[key]
        if key in comments:
            lines.append(f"# {comments[key]}")
        quoted = [f'"{u}"' for u in urls]
        if len(quoted) == 1:
            lines.append(f"{key} = [{quoted[0]}]")
        else:
            lines.append(f"{key} = [{quoted[0]},")
            for q in quoted[1:-1]:
                lines.append(f"{q},")
            lines.append(f"{quoted[-1]}]")
        lines.append("")
    return "\n".join(lines)



def extract_existing_comments(cygnet_text: str) -> dict[str, str]:
    """Extract existing comments per BCP-47 key from cygnet TOML text."""
    comments: dict[str, str] = {}
    lines = cygnet_text.splitlines()
    pending_comment = ""
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("#"):
            pending_comment = stripped.lstrip("# ").strip()
        elif stripped and not stripped.startswith("#"):
            m = re.match(r'^(\w[\w-]*)\s*=', stripped)
            if m and pending_comment:
                comments[m.group(1)] = pending_comment
                pending_comment = ""
        else:
            if not stripped:
                pending_comment = ""
    return comments


# ── markdown output ───────────────────────────────────────────────────────────

def write_changes_md(
    updates: list[dict],
    additions: list[dict],
    skipped: list[dict],
) -> str:
    lines = ["# Proposed cygnet wordnets.toml changes\n"]

    lines.append(f"## Updates ({len(updates)})\n")
    lines.append("Wordnets already in cygnet where a newer version is available.\n")
    if updates:
        lines.append("| ID | Language | Old URL | New URL | Notes |")
        lines.append("|----|----|----|----|-----|")
        for u in updates:
            old = u["old_url"]
            new = u["new_url"]
            notes = u.get("notes", "")
            lines.append(
                f"| {u['id']} | {u['language']} | `{old}` | `{new}` | {notes} |"
            )
    else:
        lines.append("_No updates identified._")
    lines.append("")

    lines.append(f"## Additions ({len(additions)})\n")
    lines.append(
        "Wordnets not currently in cygnet that can be added directly "
        "(GWA LMF source, downloads successfully).\n"
    )
    if additions:
        lines.append("| ID | Language | BCP-47 | URL | Validation | Transformations |")
        lines.append("|----|----|----|----|----|----|")
        for a in additions:
            val = a["validation"]
            val_str = "✓ ok" if val == "ok" else f"⚠ {val}"
            if a.get("error_count", 0):
                val_str += f" ({a['error_count']}E)"
            if a.get("warning_count", 0):
                val_str += f" ({a['warning_count']}W)"
            transforms = "; ".join(a.get("transformations", [])) or "—"
            lines.append(
                f"| {a['id']} | {a['language']} | {a['bcp47']} "
                f"| `{a['url']}` | {val_str} | {transforms} |"
            )
    else:
        lines.append("_No new additions identified._")
    lines.append("")

    lines.append(f"## Considered but not added ({len(skipped)})\n")
    if skipped:
        lines.append("| ID | Language | Reason |")
        lines.append("|----|----|-----|")
        for s in skipped:
            lines.append(f"| {s['id']} | {s['language']} | {s['reason']} |")
    else:
        lines.append("_All eligible wordnets were included._")
    lines.append("")

    return "\n".join(lines)


def write_close_md(close: list[dict]) -> str:
    lines = [
        "# Close-to-adoption wordnets\n",
        "These wordnets are known and nearly ready for cygnet but cannot be added",
        "directly yet. The goal is to work with upstream to resolve the blockers.\n",
    ]

    # Group by reason type
    needs_lmf = [c for c in close if "GWA LMF release" in c["reason"]]
    needs_url = [c for c in close if "URL broken" in c["reason"]]
    needs_fix = [c for c in close if c not in needs_lmf and c not in needs_url]

    if needs_lmf:
        lines.append(f"## Needs upstream GWA LMF release ({len(needs_lmf)})\n")
        lines.append(
            "These wordnets work after conversion in our pipeline but cygnet "
            "needs a direct GWA LMF URL. Ask upstream to publish a versioned "
            "GWA LMF release.\n"
        )
        lines.append("| ID | Language | Converted from | Validation | Error codes |")
        lines.append("|----|----|----|----|-----|")
        for c in needs_lmf:
            val = c["result"].get("validation", "?")
            val_str = "✓ ok" if val == "ok" else val
            ec = ", ".join(c["result"].get("error_codes", [])) or "—"
            wc = c["result"].get("warning_count", 0)
            if wc:
                val_str += f" ({wc}W)"
            lines.append(
                f"| {c['id']} | {c['language']} | {c['result'].get('converted_from', '?')} "
                f"| {val_str} | {ec} |"
            )
        lines.append("")

    if needs_url:
        lines.append(f"## Broken download URLs ({len(needs_url)})\n")
        lines.append(
            "These are GWA LMF wordnets whose download URL is broken. "
            "The wordnet needs a new URL or release.\n"
        )
        for c in needs_url:
            url = c["entry"].get("release_url") or c["entry"].get("example_file") or "?"
            lines.append(f"- **{c['id']}** ({c['language']}): `{url}`")
        lines.append("")

    if needs_fix:
        lines.append(f"## Other issues ({len(needs_fix)})\n")
        for c in needs_fix:
            lines.append(f"- **{c['id']}** ({c['language']}): {c['reason']}")
        lines.append("")

    if not close:
        lines.append("_No close-to-adoption wordnets identified._\n")

    return "\n".join(lines)


# ── cygnet build test ─────────────────────────────────────────────────────────

def run_cygnet_build(cygnet_dir: Path, proposed_toml: Path) -> bool:
    """
    Clone cygnet (or update existing clone), substitute the proposed
    wordnets.toml, run build.sh --skip-tests, report pass/fail.
    """
    if not cygnet_dir.exists():
        print(f"Cloning cygnet into {cygnet_dir} …")
        result = subprocess.run(
            ["git", "clone", "--depth=1", CYGNET_REPO_URL, str(cygnet_dir)],
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            print(f"  ✗ Clone failed: {result.stderr[:200]}")
            return False
    else:
        print(f"Updating cygnet clone at {cygnet_dir} …")
        subprocess.run(["git", "-C", str(cygnet_dir), "pull", "--ff-only"],
                       capture_output=True)

    # Backup and replace wordnets.toml
    orig = cygnet_dir / "wordnets.toml"
    backup = cygnet_dir / "wordnets.toml.bak"
    if orig.exists():
        orig.rename(backup)
    import shutil
    shutil.copy2(proposed_toml, orig)
    print(f"Replaced {orig} with proposed version.")

    print("Running cygnet build (--skip-tests) — this downloads ~700 MB …")
    result = subprocess.run(
        ["bash", "build.sh", "--skip-tests"],
        cwd=cygnet_dir,
        capture_output=False,  # stream output live
    )

    if result.returncode == 0:
        print("✓ cygnet build succeeded with proposed wordnets.toml")
        return True
    else:
        print("✗ cygnet build FAILED — review output above")
        # Restore original
        if backup.exists():
            backup.rename(orig)
            print(f"  Restored original {orig}")
        return False


# ── main ──────────────────────────────────────────────────────────────────────

def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--cygnet-dir", type=Path, default=None,
                   help="Path to existing cygnet clone (default: build/cygnet/repo)")
    p.add_argument("--test", action="store_true",
                   help="After generating, clone cygnet and run its build (~700 MB)")
    args = p.parse_args(argv)

    cygnet_dir = args.cygnet_dir or (ROOT / "build" / "cygnet" / "repo")

    results = load_results()
    if not results:
        print("ERROR: build/results.json not found — run download.py first", file=sys.stderr)
        sys.exit(1)

    entries = {e["id"]: e for e in load_toml_entries()}
    cygnet_text = fetch_cygnet_toml(cygnet_dir if args.cygnet_dir else None)
    cygnet = parse_cygnet_toml(cygnet_text)
    existing_comments = extract_existing_comments(cygnet_text)

    fp_index = build_fingerprint_index(cygnet)

    # Start proposed TOML from current cygnet content
    proposed: dict[str, list[str]] = {k: list(v) for k, v in cygnet.items()}
    # Track comment per key (names of wordnets in that language slot)
    key_names: dict[str, list[str]] = defaultdict(list)
    for key, comment in existing_comments.items():
        if key in proposed:
            key_names[key].append(comment)

    updates: list[dict] = []
    additions: list[dict] = []
    skipped: list[dict] = []
    close: list[dict] = []

    # Track which cygnet fingerprints we've already handled (to avoid double-processing)
    handled_fps: set[str] = set()

    for wn_id, result in sorted(results.items()):
        entry = entries.get(wn_id, {})
        language = entry.get("language") or result.get("name") or wn_id
        bcp47_raw = entry.get("bcp47") or result.get("bcp47") or ""
        key = cygnet_key(bcp47_raw)
        url = best_url(entry)

        if not url:
            continue

        fp = url_fingerprint(url)

        # ── Is this already in cygnet (same fingerprint)? ──
        if fp in fp_index:
            cygnet_bcp47_cur, cygnet_url = fp_index[fp]
            our_ver = url_version(url)
            their_ver = url_version(cygnet_url)
            if url == cygnet_url:
                # Exact match — no change needed
                handled_fps.add(fp)
                key_names[cygnet_bcp47_cur].append(entry.get("name", wn_id))
                continue
            if (our_ver and their_ver and is_strictly_newer(our_ver, their_ver)
                    and result.get("validation") != "parse_error"):
                # Same wordnet, newer version available (skip if our copy is unparseable)
                proposed[cygnet_bcp47_cur] = [
                    url if u == cygnet_url else u
                    for u in proposed[cygnet_bcp47_cur]
                ]
                updates.append({
                    "id": wn_id,
                    "language": language,
                    "old_url": cygnet_url,
                    "new_url": url,
                    "notes": f"{their_ver} → {our_ver}",
                })
                handled_fps.add(fp)
                key_names[cygnet_bcp47_cur].append(entry.get("name", wn_id))
            continue

        # ── Not in cygnet — classify ──
        if is_cygnet_eligible(entry, result):
            if key not in proposed:
                proposed[key] = []
            proposed[key].append(url)
            key_names[key].append(entry.get("name", wn_id))
            additions.append({
                "id": wn_id,
                "language": language,
                "bcp47": key,
                "url": url,
                "validation": result.get("validation", "?"),
                "error_count": result.get("error_count", 0),
                "warning_count": result.get("warning_count", 0),
                "transformations": result.get("transformations", []),
            })
        else:
            close_reason = close_to_adoption_reason(entry, result)
            if close_reason:
                close.append({
                    "id": wn_id,
                    "language": language,
                    "reason": close_reason,
                    "entry": entry,
                    "result": result,
                })
            else:
                dl = result.get("download", "?")
                if dl != "ok" and entry.get("format", "") not in ("GWA LMF", ""):
                    # Not tried / unknown format — skip silently
                    continue
                if dl != "ok":
                    skipped.append({
                        "id": wn_id,
                        "language": language,
                        "reason": f"Download {dl}",
                    })
                elif not result.get("xml"):
                    skipped.append({
                        "id": wn_id,
                        "language": language,
                        "reason": "No XML produced (format not yet convertible)",
                    })

    # Build per-key comment strings
    comments: dict[str, str] = {}
    for key, names in key_names.items():
        seen = []
        for n in names:
            if n not in seen:
                seen.append(n)
        if key in existing_comments and not any(
            n in existing_comments[key] for n in seen
        ):
            comments[key] = existing_comments[key] + ", " + ", ".join(seen)
        elif key in existing_comments:
            comments[key] = existing_comments[key]
        else:
            comments[key] = ", ".join(seen)

    # ── Write outputs ──
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    proposed_toml_path = OUT_DIR / "wordnets.toml"
    proposed_toml_path.write_text(write_proposed_toml(proposed, comments))

    changes_path = OUT_DIR / "CHANGES.md"
    changes_path.write_text(write_changes_md(updates, additions, skipped))

    close_path = OUT_DIR / "close_to_adoption.md"
    close_path.write_text(write_close_md(close))

    # ── Summary ──
    print(f"\n── Results ────────────────────────────────────────────────────")
    print(f"  Updates:              {len(updates)}")
    print(f"  Additions:            {len(additions)}")
    print(f"  Close to adoption:    {len(close)}")
    print(f"  Skipped:              {len(skipped)}")
    print(f"\n── Outputs ────────────────────────────────────────────────────")
    print(f"  {proposed_toml_path.relative_to(ROOT)}")
    print(f"  {changes_path.relative_to(ROOT)}")
    print(f"  {close_path.relative_to(ROOT)}")

    if updates:
        print("\n── Updates ────────────────────────────────────────────────────")
        for u in updates:
            print(f"  {u['id']:30s} {u['notes']}")

    if additions:
        print("\n── Additions ──────────────────────────────────────────────────")
        for a in additions:
            flag = "⚠" if a.get("error_count") else "✓"
            print(f"  {flag} {a['id']:28s} [{a['bcp47']}]  {a['validation']}")

    if close:
        print("\n── Close to adoption ──────────────────────────────────────────")
        for c in close:
            print(f"  {c['id']:30s} {c['reason'][:70]}")

    if args.test:
        print()
        ok = run_cygnet_build(cygnet_dir, proposed_toml_path)
        sys.exit(0 if ok else 1)

    return 0


if __name__ == "__main__":
    sys.exit(main())
