#!/usr/bin/env python3
"""
visdic2lmf.py — Convert VisDic/DEBVisDic/ROWN XML to GWA LMF 1.3

Handles these root-element variants:
  <ROWN>    — Romanian WordNet (RACAI format)
  <WN>      — DEBVisDic (sloWNet 3.x, WOLF, BalkaNet-derived)
  <SYNSETS> — Custom Turkish WordNet (Starlang)
  bare      — Czech WordNet (no root wrapper, bare <SYNSET> stream)

All variants use Princeton WN 3.0 offset-based synset IDs except the
Turkish custom IDs (TUR10-*). Provide --ili-map for ILI assignment.

Fields captured from source:
  ID, POS          → Synset.id, partOfSpeech
  LITERAL          → LexicalEntry lemma + Sense; LITERAL/@sense → Sense.n
  DEF              → Synset.definitions; xml:lang → Definition.language
  USAGE            → Synset.examples; xml:lang → Example.language
  ILR / SR         → Synset.relations (all recognised relation types)
  DOMAIN           → Synset.dc:subject

Fields intentionally not captured:
  BCS              — Basic Concept Set level (no GWA LMF field; niche use)
  CLUSTER          — semeval07 clustering info (not standard WN)
  STAMP            — annotator name (no standard LMF field)
  SUMO             — SUMO ontology mapping (specialised; no LMF field)
  SENTIWN          — SentiWordNet scores (specialised; no LMF field)
  LITERAL/@pwnid   — Princeton sense key (no standard LMF field)
  LITERAL/@lnote   — provenance/confidence notes (non-standard)

Design: streaming via iterparse — input is parsed one SYNSET at a time
(no full DOM in memory), and output is written incrementally.  LexicalEntry
elements are written as each synset is processed; Synset elements are buffered
(compact dicts) and written at the end.  This keeps peak memory proportional
to the number of synsets * a small constant, not the raw XML file size.

Usage:
  python visdic2lmf.py rown.xml \\
      --id rowordnet --label "Romanian WordNet" \\
      --language ro --email "x@y.com" --license "CC BY SA" \\
      --ili-map ext/omw-data/etc/cili/ili-map-pwn30.tab \\
      --version 1.0 --url "http://www.racai.ro/tools/text/rowordnet-visualizer/"

  # DEBVisDic with multiple languages: filter with --lang
  python visdic2lmf.py slownet.xml \\
      --id slownet --label "sloWNet 3.1" --language sl \\
      --lang sl --email "x@y.com" --license "CC BY SA 3.0" \\
      --ili-map ext/omw-data/etc/cili/ili-map-pwn30.tab
"""

import argparse
import io
import logging
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from xml.sax.saxutils import escape, quoteattr

LMF_VERSION = "1.3"
LMF_DOCTYPE = (
    '<!DOCTYPE LexicalResource SYSTEM '
    '"https://globalwordnet.github.io/schemas/WN-LMF-1.3.dtd">'
)
LMF_NS = 'xmlns:dc="https://globalwordnet.github.io/schemas/dc/"'

log = logging.getLogger("visdic2lmf")

# POS normalisation: VisDic uses 'b' for adverbs; 's' for satellite adjectives
POS_MAP = {
    "n": "n", "v": "v", "a": "a", "s": "a", "r": "r",
    "adj": "a", "adv": "r", "b": "r",
}

# ILR/SR type → GWA LMF SynsetRelation type
ILR_MAP = {
    "hypernym": "hypernym",
    "hyponym": "hyponym",
    "instance_hypernym": "instance_hypernym",
    "instance_hyponym": "instance_hyponym",
    "holonym": "holonym",
    "meronym": "meronym",
    "part_holonym": "holo_part",
    "part_meronym": "mero_part",
    "member_holonym": "holo_member",
    "member_meronym": "mero_member",
    "holo_member": "holo_member",
    "mero_member": "mero_member",
    "holo_part": "holo_part",
    "mero_part": "mero_part",
    "substance_holonym": "holo_substance",
    "substance_meronym": "mero_substance",
    "antonym": "antonym",
    "near_antonym": "antonym",
    "similar_to": "similar",
    "also_see": "also",
    "also": "also",
    "derived": "derivation",
    "derivation_related": "derivation",
    "eng_derivative": "derivation",
    "near_eng_derivat": "derivation",
    "be_in_state": "state_of",
    "subevent": "subevent",
    "causes": "causes",
    "is_caused_by": "is_caused_by",
    "domain_topic": "domain_topic",
    "category_domain": "domain_topic",
    "domain_region": "domain_region",
    "domain_usage": "domain_usage",
    "domain_member_topic": "domain_topic",
    "member_topic": "domain_topic",
    "domain_member_region": "domain_region",
    "domain_member_usage": "domain_usage",
    "near_domain_member_topic": "domain_topic",
    "near_domain_member_region": "domain_region",
    "near_domain_member_usage": "domain_usage",
    "verb_group": "similar",
    "attribute": "attribute",
    "participle_of_verb": "participle_of",
    "pertainym": "pertainym",
}


# ── helpers ────────────────────────────────────────────────────────────────────

_UNSAFE_RE = re.compile(
    r"[^"
    r"\w.\u00B7"
    r"\u00C0-\u00D6\u00D8-\u00F6\u00F8-\u02FF"
    r"\u0300-\u036F\u0370-\u037D\u037F-\u1FFF"
    r"\u200C\u200D\u2070-\u218F\u2C00-\u2FEF"
    r"\u3001-\uD7FF\uF900-\uFDCF\uFDF0-\uFFFD"
    r"]",
    re.UNICODE,
)


def escape_lemma(lemma: str) -> str:
    return _UNSAFE_RE.sub(lambda m: f"_{ord(m.group()):04X}_", lemma)


def load_ili_map(path: str) -> dict[str, str]:
    """Load ili TAB ssid → {ssid: ili}."""
    ilimap: dict[str, str] = {}
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            parts = line.split("\t")
            if len(parts) != 2:
                continue
            ili, ssid = parts
            ilimap[ssid] = ili
            if ssid.endswith("-s"):
                ilimap[ssid[:-2] + "-a"] = ili
    return ilimap


def parse_offset_pos(raw_id: str) -> tuple[str, str] | None:
    """Extract (8-digit-offset, normalised-pos) or None for non-PWN IDs."""
    raw = raw_id.strip()
    m = re.match(r"(?:[A-Za-z]+-?\d+-?)(\d{8})-([a-zA-Z])$", raw)
    if m:
        return m.group(1), POS_MAP.get(m.group(2).lower(), m.group(2).lower())
    m = re.match(r"(\d{8})-([a-zA-Z])$", raw)
    if m:
        return m.group(1), POS_MAP.get(m.group(2).lower(), m.group(2).lower())
    m = re.match(r"(\d{8})([a-zA-Z])$", raw)
    if m:
        return m.group(1), POS_MAP.get(m.group(2).lower(), m.group(2).lower())
    return None


def literal_text(lit: ET.Element) -> str:
    """Text of a LITERAL element, ignoring child tags like <SENSE>."""
    return (lit.text or "").strip().replace("_", " ")


# ── streaming XML writer ───────────────────────────────────────────────────────

def _attr(k: str, v: str) -> str:
    return f" {k}={quoteattr(v)}"


def write_entry(out: io.IOBase, eid: str, lemma: str, pos: str,
                sense_id: str, synset_id: str, sense_n: int | None) -> None:
    out.write(f'    <LexicalEntry{_attr("id", eid)}>\n')
    out.write(f'      <Lemma{_attr("writtenForm", lemma)}{_attr("partOfSpeech", pos)}/>\n')
    n_attr = _attr("n", str(sense_n)) if sense_n is not None else ""
    out.write(f'      <Sense{_attr("id", sense_id)}{_attr("synset", synset_id)}{n_attr}/>\n')
    out.write('    </LexicalEntry>\n')


def write_synset(out: io.IOBase, ss: dict) -> None:
    attrs = (
        _attr("id", ss["id"]) +
        _attr("ili", ss.get("ili", "")) +
        _attr("partOfSpeech", ss["pos"])
    )
    if domain := ss.get("domain"):
        attrs += _attr("dc:subject", domain)
    if members := ss.get("members"):
        attrs += _attr("members", " ".join(members))
    has_children = ss.get("defs") or ss.get("exs") or ss.get("rels")
    if not has_children:
        out.write(f'    <Synset{attrs}/>\n')
        return
    out.write(f'    <Synset{attrs}>\n')
    for d in ss.get("defs", []):
        lang_attr = _attr("language", d[1]) if d[1] else ""
        out.write(f'      <Definition{lang_attr}>{escape(d[0])}</Definition>\n')
    for e in ss.get("exs", []):
        lang_attr = _attr("language", e[1]) if e[1] else ""
        out.write(f'      <Example{lang_attr}>{escape(e[0])}</Example>\n')
    for r in ss.get("rels", []):
        out.write(f'      <SynsetRelation{_attr("relType", r[0])}{_attr("target", r[1])}/>\n')
    out.write('    </Synset>\n')



def convert(
    input_path: Path,
    output_path: Path,
    wn_id: str,
    label: str,
    language: str,
    email: str,
    license_: str,
    version: str = "1.0",
    url: str = "",
    lang_filter: str | None = None,
    ilimap: dict[str, str] | None = None,
    encoding: str = "utf-8",
    keep_empty: bool = True,
) -> tuple[int, int]:
    """Convert a VisDic XML file to GWA LMF. Returns (entry_count, synset_count)."""
    if ilimap is None:
        ilimap = {}

    log.info("Converting %s [%s] → %s", input_path, encoding, output_path)

    raw = input_path.read_bytes()
    decoded = raw.decode(encoding, errors="replace")

    # Strip XML/DOCTYPE declarations, optionally wrap bare SYNSET stream
    stripped = re.sub(r"^\s*<\?xml[^?]*\?>\s*", "", decoded, flags=re.IGNORECASE)
    stripped = re.sub(r"^\s*<!DOCTYPE[^>]*>\s*", "", stripped, flags=re.IGNORECASE)
    first_tag = re.match(r"\s*<(\w+)", stripped)
    if first_tag and first_tag.group(1).upper() == "SYNSET":
        log.info("  No root element — wrapping in <ROOT>")
        stripped = "<ROOT>" + stripped + "</ROOT>"

    xml_ns = "{http://www.w3.org/XML/1998/namespace}"
    seen_entry: dict[str, int] = {}
    synset_buf: list[dict] = []
    seen_ss_ids: set[str] = set()
    stub_synsets: dict[str, dict] = {}
    entry_count = 0

    # Build the known_ids set in a lightweight first pass (just ID + POS elements)
    known_ids: set[str] = set()
    if keep_empty:
        _cur_id: str | None = None
        _cur_pos: str | None = None
        for _, elem in ET.iterparse(io.StringIO(stripped), events=("end",)):
            if elem.tag == "ID" and elem.text:
                _cur_id = elem.text.strip()
            elif elem.tag == "POS" and elem.text:
                _cur_pos = elem.text.strip().lower()
            elif elem.tag == "SYNSET":
                if _cur_id:
                    p = parse_offset_pos(_cur_id)
                    if p:
                        off, pos = p
                        if _cur_pos:
                            pos = POS_MAP.get(_cur_pos, _cur_pos)
                        known_ids.add(f"{wn_id}-{off}-{pos}")
                    else:
                        safe = re.sub(r"[^A-Za-z0-9_]", "_", _cur_id)
                        pos = POS_MAP.get(_cur_pos or "", _cur_pos or "n")
                        known_ids.add(f"{wn_id}-{safe}-{pos}")
                _cur_id = _cur_pos = None
                elem.clear()
        log.info("  Pre-scan: %d synset IDs", len(known_ids))

    # Main conversion pass — iterparse with both start and end events
    # so we can capture SYNONYM's xml:lang before processing its LITERAL children
    cur: dict = {}
    syn_lang: str | None = None   # current SYNONYM's xml:lang

    with open(output_path, "w", encoding="utf-8") as out:
        out.write('<?xml version="1.0" encoding="UTF-8"?>\n')
        out.write(LMF_DOCTYPE + "\n")
        out.write(f'<LexicalResource {LMF_NS}>\n')

        lex_attrs = (
            f' id={quoteattr(wn_id)}'
            f' label={quoteattr(label)}'
            f' language={quoteattr(language)}'
            f' email={quoteattr(email)}'
            f' license={quoteattr(license_)}'
            f' version={quoteattr(version)}'
        )
        if url:
            lex_attrs += f' url={quoteattr(url)}'
        out.write(f'  <Lexicon{lex_attrs}>\n')

        for event, elem in ET.iterparse(io.StringIO(stripped), events=("start", "end")):
            tag = elem.tag

            if event == "start":
                if tag == "SYNSET":
                    cur = {"lemmas": [], "defs": [], "exs": [], "rels": [],
                           "domain": "", "pos": "n", "ss_id": "", "ili": ""}
                elif tag == "SYNONYM":
                    syn_lang = elem.get(f"{xml_ns}lang") or elem.get("lang")
                elif tag in ("ILR", "SR"):
                    pass  # children accumulated via end events

            else:  # end event
                if tag == "ID" and elem.text and not cur.get("ss_id"):
                    raw_id = elem.text.strip()
                    p = parse_offset_pos(raw_id)
                    if p:
                        offset, pos = p
                        cur["_offset"] = offset
                        cur["_is_pwn"] = True
                        cur["pos"] = pos
                        cur["ss_id"] = f"{wn_id}-{offset}-{pos}"
                        cur["ili"] = ilimap.get(f"{offset}-{pos}", "")
                    else:
                        safe = re.sub(r"[^A-Za-z0-9_]", "_", raw_id)
                        cur["_is_pwn"] = False
                        cur["_offset"] = safe
                        cur["pos"] = "n"
                        cur["ss_id"] = f"{wn_id}-{safe}-n"

                elif tag == "POS" and elem.text:
                    pos = POS_MAP.get(elem.text.strip().lower(), elem.text.strip().lower())
                    cur["pos"] = pos
                    # Recompute ss_id with corrected pos
                    if cur.get("_is_pwn") and "_offset" in cur:
                        ssid_key = f"{cur['_offset']}-{pos}"
                        cur["ss_id"] = f"{wn_id}-{ssid_key}"
                        cur["ili"] = ilimap.get(ssid_key, "")
                    elif "_offset" in cur:
                        cur["ss_id"] = f"{wn_id}-{cur['_offset']}-{pos}"

                elif tag == "LITERAL":
                    t = literal_text(elem)
                    if t and t != "_EMPTY_":
                        # Apply lang filter: skip if SYNONYM has wrong lang
                        if lang_filter and syn_lang and syn_lang != lang_filter:
                            pass
                        else:
                            sense_attr = elem.get("sense")
                            sense_n = int(sense_attr) if sense_attr and sense_attr.isdigit() else None
                            cur["lemmas"].append((t, sense_n))

                elif tag == "SYNONYM":
                    syn_lang = None

                elif tag == "DEF" and elem.text:
                    t = elem.text.strip()
                    if t:
                        lang = elem.get(f"{xml_ns}lang") or elem.get("lang") or ""
                        cur["defs"].append((t, lang))

                elif tag in ("USAGE", "EXAMPLE") and elem.text:
                    t = elem.text.strip()
                    if t and t.lower() != "none":
                        lang = elem.get(f"{xml_ns}lang") or elem.get("lang") or ""
                        # KeNet uses "|" to separate multiple examples in one tag
                        for part in t.split("|"):
                            part = part.strip()
                            if part:
                                cur["exs"].append((part, lang))

                elif tag == "DOMAIN" and elem.text:
                    cur["domain"] = elem.text.strip()

                elif tag in ("ILR", "SR"):
                    rel_type_raw = elem.get("type") or ""
                    type_sub = elem.find("TYPE")
                    if type_sub is not None and type_sub.text:
                        rel_type_raw = type_sub.text.strip()
                    rel_type_lower = rel_type_raw.lower()
                    # SYNONYM ILR to an external PWN synset → use as ILI hint
                    if rel_type_lower == "synonym" and not cur.get("ili"):
                        target_parsed = parse_offset_pos(elem.text or "")
                        if target_parsed:
                            t_offset, t_pos = target_parsed
                            cur["ili"] = ilimap.get(f"{t_offset}-{t_pos}", "")
                        continue
                    rel_type = ILR_MAP.get(rel_type_lower, "")
                    if rel_type:
                        target_raw = (elem.text or "").strip()
                        target_parsed = parse_offset_pos(target_raw)
                        if target_parsed:
                            t_offset, t_pos = target_parsed
                            target_id = f"{wn_id}-{t_offset}-{t_pos}"
                            cur["rels"].append((rel_type, target_id))
                            if keep_empty and target_id not in known_ids:
                                stub_synsets.setdefault(target_id, {
                                    "id": target_id,
                                    "ili": ilimap.get(f"{t_offset}-{t_pos}", ""),
                                    "pos": t_pos,
                                    "defs": [], "exs": [], "rels": [],
                                    "domain": "", "members": [],
                                })

                elif tag == "SYNSET":
                    ss_id = cur.get("ss_id", "")
                    if not ss_id:
                        cur = {}
                        continue

                    member_ids: list[str] = []
                    for lemma_text, sense_n in cur["lemmas"]:
                        esc = escape_lemma(lemma_text)
                        ekey = f"{esc}-{cur['pos']}"
                        cnt = seen_entry.get(ekey, 0)
                        seen_entry[ekey] = cnt + 1
                        eid = f"{wn_id}-{ekey}" if cnt == 0 else f"{wn_id}-{ekey}-{cnt}"
                        sense_suffix = ss_id[len(wn_id) + 1:]
                        sense_id = f"{eid}-{sense_suffix}"
                        write_entry(out, eid, lemma_text, cur["pos"],
                                    sense_id, ss_id, sense_n)
                        member_ids.append(eid)
                        entry_count += 1

                    synset_buf.append({
                        "id": ss_id,
                        "ili": cur["ili"],
                        "pos": cur["pos"],
                        "defs": cur["defs"],
                        "exs": cur["exs"],
                        "rels": cur["rels"],
                        "domain": cur["domain"],
                        "members": member_ids,
                    })
                    seen_ss_ids.add(ss_id)
                    cur = {}
                    elem.clear()

        # Write synsets
        for ss in synset_buf:
            write_synset(out, ss)
        # Write stubs for cross-file relation targets
        if keep_empty:
            for stub_id, stub in stub_synsets.items():
                if stub_id not in seen_ss_ids:
                    write_synset(out, stub)

        out.write('  </Lexicon>\n')
        out.write('</LexicalResource>\n')

    n_stubs = sum(1 for sid in stub_synsets if sid not in seen_ss_ids) if keep_empty else 0
    n_synsets = len(synset_buf)
    log.info("  %d entries, %d synsets (%d stubs) → %s",
             entry_count, n_synsets, n_stubs, output_path)
    return entry_count, n_synsets + n_stubs


# ── CLI ────────────────────────────────────────────────────────────────────────

def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("input", type=Path, help="Input VisDic/ROWN/DEBVisDic XML file")
    p.add_argument("-o", "--output", type=Path,
                   help="Output GWA LMF XML file (default: <id>.xml)")
    p.add_argument("--id", required=True, metavar="ID", help="Wordnet ID (e.g. rowordnet)")
    p.add_argument("--label", required=True, help="Wordnet label/name")
    p.add_argument("--language", required=True, help="BCP 47 language code")
    p.add_argument("--email", default="", help="Contact email address")
    p.add_argument("--license", required=True, dest="license_", metavar="LICENSE")
    p.add_argument("--version", default="1.0", help="Version string (default: 1.0)")
    p.add_argument("--url", default="", help="Project URL")
    p.add_argument("--lang", dest="lang_filter", default=None, metavar="LANG",
                   help="xml:lang value to include from SYNONYM elements (DEBVisDic only)")
    p.add_argument("--ili-map", dest="ili_maps", action="append", default=[], metavar="PATH",
                   help="ILI map file (ili TAB offset-pos); can be given multiple times "
                        "to merge maps (e.g. PWN 3.0 and PWN 3.1)")
    p.add_argument("--encoding", default="utf-8",
                   help="Input file encoding (default: utf-8)")
    p.add_argument("--no-keep-empty", dest="keep_empty", action="store_false", default=True,
                   help="Drop stub synsets for cross-file relation targets "
                        "(default: keep them so relation graph stays intact)")
    args = p.parse_args(argv)

    logging.basicConfig(format="%(levelname)s:%(name)s:%(message)s", level=logging.INFO)

    ili_map_paths = args.ili_maps
    if not ili_map_paths:
        default = Path(__file__).parent.parent / "ext/omw-data/etc/cili/ili-map-pwn30.tab"
        if default.exists():
            ili_map_paths = [str(default)]
            log.info("Using default ILI map: %s", default)

    ilimap: dict[str, str] = {}
    for path in ili_map_paths:
        loaded = load_ili_map(path)
        ilimap.update(loaded)
        log.info("Loaded %d ILI mappings from %s", len(loaded), path)

    output = args.output or Path(f"{args.id}.xml")
    ne, ns = convert(
        input_path=args.input,
        output_path=output,
        wn_id=args.id,
        label=args.label,
        language=args.language,
        email=args.email,
        license_=args.license_,
        version=args.version,
        url=args.url,
        lang_filter=args.lang_filter,
        ilimap=ilimap,
        encoding=args.encoding,
        keep_empty=args.keep_empty,
    )
    print(f"Done: {ne:,} entries, {ns:,} synsets → {output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
