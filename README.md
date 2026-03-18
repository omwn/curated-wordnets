# curated-wordnets

A curated catalogue of wordnets across languages, with download, validation,
and citation tooling.

**Goal:** find and make accessible as many wordnets as possible — ideally in
[GWA LMF](https://globalwordnet.github.io/schemas/) format, validated, and
under an open licence.  The data is intended to feed into:

- [GWA Wordnets in the World](https://globalwordnet.github.io/gwadoc/)
- [wn module `index.toml`](https://wn.readthedocs.io/)
- [cygnet `wordnets.toml`](https://github.com/omwn/cygnet)

For each wordnet we aim to record:
- Download/release URL (ideally a versioned package with LICENSE, README, citation;
  see [wn LMF packages](https://wn.readthedocs.io/en/latest/guides/lexicons.html#wn-lmf-files-packages-and-collections))
- Format and validation status
- Open licence
- Type: expand / merge / extend / auto-generated
- Whether it supersedes an earlier wordnet

## Current status

| Metric | Count |
|--------|-------|
| Total catalogued | 121 |
| **Validated OK** | **74** |
| — fully clean | 44 |
| — with warnings only | 30 |
| XML parse errors | 4 |
| Download OK | 80 |
| Download failed / restricted | 18 |

See [SUMMARY.md](SUMMARY.md) for the full per-wordnet table.
Regenerate with `uv run python scripts/summary.py > SUMMARY.md`.

## Repository layout

```
wordnets_found.toml   # canonical catalogue — one [[wordnet]] block per entry
citations/            # generated BibTeX files, one per wordnet
scripts/
  download.py         # download + validate pipeline
  summary.py          # statistics + Markdown table
  make_citations.py   # generate citations/ from TOML acl_ids / bib fields
  visdic2lmf.py       # VisDic/ROWN/DEBVisDic XML → GWA LMF converter
tests/
  test_integration.py # slow network tests (pytest --run-slow)
build/                # generated — gitignored
  raw/                # original downloaded files
  pkg/                # processed GWA LMF XML files
  results.json        # download + validation results
```

## TOML schema

Each `[[wordnet]]` entry in `wordnets_found.toml` has:

| Field | Required | Description |
|-------|----------|-------------|
| `id` | ✓ | Short identifier, used as filename stem |
| `name` | ✓ | Human-readable name |
| `language` | ✓ | Language name |
| `bcp47` | ✓ | BCP-47 language tag |
| `format` | ✓ | Data format (see below) |
| `confidence` | ✓ | `high` / `medium` / `low` |
| `repo_url` | | Primary URL (GitHub, project page) |
| `release_url` | | Direct download URL (preferred over `example_file`) |
| `example_file` | | URL to a single data file |
| `license` | | SPDX expression or common name |
| `known_in_cygnet` | | `true` if already in [cygnet](https://github.com/omwn/cygnet) |
| `notes` | | Free-text notes |
| `acl_ids` | | List of ACL Anthology paper IDs for citation |
| `bib` | | Raw BibTeX string (overrides `acl_ids`) |
| `type` | | Construction method (see below) |
| `supersedes` | | List of `id`s this entry replaces |

**Format values:** `GWA LMF`, `OMW 1.0 tab`, `Princeton WNDB`, `RDF/TTL`,
`VisDic XML`, `DanNet TAB`, `custom XML`, `YAML`, `GF`, `Lemon TTL`,
`alignment TSV`, `JSON`, `unknown`

**Type values:**
- `expand` — built by expanding/translating Princeton WordNet synsets
- `standalone` — built independently from scratch
- `merge` — built by merging multiple existing wordnets
- `auto` — automatically generated (e.g. from corpora or alignments)
- `extension` — extends an existing wordnet with additional entries
- `alignment` — a mapping/alignment between wordnets or to another resource

## Scripts

All scripts live in `scripts/` and are run from the project root.

### `download.py` — download, convert, validate

```bash
uv run python scripts/download.py --all                      # all high-confidence
uv run python scripts/download.py --ids oewn odenet          # specific entries
uv run python scripts/download.py --all --phase validate     # re-validate only
uv run python scripts/download.py --all --confidence medium  # include medium
```

Pipeline per entry:
1. **Download** — tries `release_url` → `example_file` → `repo_url`
2. **Convert** — OMW 1.0 tab → GWA LMF (`tsv2lmf.py`); VisDic XML → GWA LMF (`visdic2lmf.py`)
3. **Validate** — runs `wn.validate()` against the GWA LMF XML

### `summary.py` — statistics + table

```bash
uv run python scripts/summary.py --stats      # stats only
uv run python scripts/summary.py > SUMMARY.md # full Markdown table
```

### `make_citations.py` — generate BibTeX

```bash
uv run python scripts/make_citations.py           # all entries
uv run python scripts/make_citations.py --id oewn # single entry
uv run python scripts/make_citations.py --no-fetch # skip network
```

Citation sources (priority order):
1. `bib` field in TOML
2. `acl_ids` field in TOML → fetches from [ACL Anthology](https://aclanthology.org/)
3. `citation=` attribute in the downloaded GWA LMF XML
4. Minimal stub

### `visdic2lmf.py` — VisDic/DEBVisDic/ROWN → GWA LMF

```bash
uv run python scripts/visdic2lmf.py input.xml output.xml
```

## Formats and conversion status

| Format | Converter | Status |
|--------|-----------|--------|
| GWA LMF | — (native) | ✓ |
| OMW 1.0 tab | `tsv2lmf.py` (omwn/omw-data) | ✓ |
| VisDic / DEBVisDic / ROWN XML | `visdic2lmf.py` | ✓ |
| ISO LMF (old `<LexicalResource>`) | needs custom converter | ✗ |
| RDF/TTL (Lemon/OntoLex) | needs rdflib converter | ✗ |
| DanNet TAB | needs DanNet converter | ✗ |
| Princeton WNDB | needs NLTK/wn tools | ✗ |

## How to find more wordnets

- Search GitHub (wordnet+lmf, open+wordnet, etc.)
- CLARIN VLO / LINDAT / Kielipankki / PORTULAN CLARIN
- GWA [Wordnets in the World](https://globalwordnet.github.io/gwadoc/)
- GWC proceedings (ACL Anthology: [gwc](https://aclanthology.org/venues/gwc/),
  [lrec](https://aclanthology.org/venues/lrec/))

If a wordnet is in OMW 1.0 tab format, the workflow is:
clone to omwn, convert with `tsv2lmf.py`, and submit a PR.

## Tests

```bash
uv run pytest                  # fast unit tests only
uv run pytest --run-slow       # include network integration tests
```

## Licence

This project is CC BY 4.0.  Individual wordnets have their own licences —
see the `license` field in `wordnets_found.toml` and `citations/` for details.
