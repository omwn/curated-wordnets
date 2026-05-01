# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this project is

A curated catalogue of wordnets across languages. The goal is to collect, convert, and validate wordnets into GWA LMF (Global Wordnet Alliance Lexical Markup Framework) format. The catalogue drives the GWA "Wordnets in the World" page and the `wn` Python library's index.

## Key commands

```bash
# Full rebuild: download + convert + validate all high-confidence wordnets
uv run python scripts/download.py --all

# Specific entries or phases
uv run python scripts/download.py --ids oewn odenet
uv run python scripts/download.py --all --phase download    # download only
uv run python scripts/download.py --all --phase validate    # validate only
uv run python scripts/download.py --all --confidence medium # include medium-confidence
uv run python scripts/download.py --ids oewn --force        # re-download

# Regenerate SUMMARY.md (do this after any rebuild or script change)
uv run python scripts/summary.py > SUMMARY.md

# Tests
uv run pytest                    # unit tests only (no network)
uv run pytest --run-slow         # include network integration tests
uv run pytest tests/test_helpers.py
```

## Pipeline overview

```
wordnets_found.toml
      │
      ▼
scripts/download.py  ──►  build/raw/{id}/       (original files, never modified)
      │                   build/pkg/{id}/{id}.xml (normalised GWA LMF)
      │                   build/pkg/{id}/download.log
      │                   build/pkg/{id}/validation.log
      ▼
build/results.json   ──►  scripts/summary.py  ──►  SUMMARY.md
```

`build/` is gitignored except `build/results.json`, which is tracked.

## wordnets_found.toml

The master catalogue. One `[[wordnet]]` block per entry, sorted alphabetically by `language` then `id`. Key fields:

- `format` — the **source** format, not the output. `"GWA LMF"` means no conversion needed. Other values (`"OMW 1.0 tab"`, `"VisDic XML"`, `"RDF/TTL"`, etc.) describe what the source ships as.
- `confidence` — `"high"`, `"medium"`, `"low"`. Missing defaults to `"low"`.
- `type` — `"expand"` means the wordnet was built by translating PWN synsets and needs `<Requires ref="omw-en">` injected and ILI attributes mapped.
- `release_url` → `example_file` → `repo_url` — tried in this order; first success wins.
- `zip_entry` — filename to extract when an archive contains multiple language files.

When a new release changes the source format (e.g. a tab-based wordnet switches to shipping XML), update `format` in the TOML to match.

## build/results.json

Tracks per-wordnet status. Important fields:

- `download`: `"ok"` / `"failed"` / `"skipped"`
- `format`: output format (always `"GWA LMF"` on success)
- `converted_from`: set only if a format conversion was applied (e.g. `"OMW 1.0 tab"`, `"VisDic XML"`)
- `transformations`: list of XML patches applied during normalisation — e.g. `["Lexicon attrs patched", "DTD upgraded 1.0→1.1", "Requires added", "stub synsets (5714)"]`
- `validation`: `"ok"` / `"errors"` / `"parse_error"` / `"skipped"`
- `error_count`, `warning_count`, `details`: wn.validate output

## Download pipeline details

**URL handling:** bare `github.com/owner/repo` URLs trigger the GitHub API to find data files. `github.com/.../blob/...` URLs are auto-converted to raw URLs.

**Format conversion:**
- `OMW 1.0 tab` → `scripts/omw-data/tsv2lmf.py` (cloned from omwn/omw-data on first use into `ext/omw-data/`). Falls back to an online converter. Some tab files have a 3-field header missing the URL; `_fix_tab_header()` patches this in-place before conversion.
- `VisDic XML` → `scripts/visdic2lmf.py`. Handles `<WN>`, `<ROWN>`, `<SYNSETS>`, and bare `<SYNSET>` stream roots. Use `--lang` to filter a single language from DEBVisDic multilingual files.

**XML normalisation** (`_install_xml`) applied to every XML file:
1. Reformat XML declaration and DOCTYPE whitespace
2. Patch missing `license=` / `email=` on `<Lexicon>` tag from TOML
3. Upgrade `WN-LMF-1.0.dtd` → `WN-LMF-1.1.dtd` for expand-type wordnets (needed for `<Requires>`)
4. Inject `<Requires ref="omw-en" version="2.0" />` for expand-type wordnets with ILI references
5. Add empty stub `<Synset>` elements for any synset IDs referenced by senses but not defined — this works around source data bugs (notable in AfWN)

All transformations are written to `results.json["transformations"]`.

## Language / BCP-47 notes

- ILI mapping uses PWN 3.0 offsets from `ext/omw-data/etc/cili/ili-map-pwn30.tab`
- Satellite adjectives (POS `s`) are normalised to `a` during tab conversion
- TUFS archive filenames use different codes than BCP-47 ids: `tufs-ar` → `tufs-arb`, `tufs-zh` → `tufs-cmn`, `tufs-ms` → `tufs-zsm`, `tufs-pb` → `tufs-pb` (Brazilian Portuguese `pt-BR`, not Punjabi)
- VisDic `xml:lang` uses 2-letter ISO codes; `--lang` filter uses first 2 chars of BCP-47

## summary.py outputs

Generates three Markdown sections:
1. **Wordnet Summary** — aggregate counts (total, confidence breakdown, download/validation status)
2. **Full Wordnet Table** — one row per TOML entry with Status and Conversion columns
3. **Content Statistics** — for readable XML only: synset/word/definition/example counts and ILI%

`Conversion` column logic: shows `"from {source format}"` if converted, lists `transformations` from results.json, or `"—"` if untouched native GWA LMF. Entries not yet successfully processed show their source format as a hint.

## ext/ and build/ roles

- `ext/omw-data/` — shallow clone of omwn/omw-data, gitignored. Created on demand. Contains `tsv2lmf.py` and the ILI map.
- `build/raw/{id}/` — original downloaded files, never modified.
- `build/pkg/{id}/` — processed outputs: `{id}.xml`, `download.log`, `validation.log`, optionally `LICENSE`, `README.md`, `citation.bib`.
- `build/results.json` — gitignored directory exception; tracked in repo.
