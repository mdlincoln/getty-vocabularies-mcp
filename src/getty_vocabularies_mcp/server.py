# getty-vocabularies-mcp — MCP server for Getty Vocabulary APIs
# Copyright (C) 2026  May S. Chan (University of Toronto)
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.

from mcp.server.fastmcp import FastMCP
import requests
import traceback
import json

mcp = FastMCP("getty vocabularies mcp server")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Keyword search — Getty OpenRefine reconciliation service
# Supports AAT, TGN, ULAN. Does not support CONA or IA (not yet LOD).
RECONCILE_ENDPOINT = "https://services.getty.edu/vocab/reconcile/"

# Full record retrieval — Getty LOD JSON-LD
# Pattern: https://vocab.getty.edu/{vocab}/{id}.json
LOD_BASE = "https://vocab.getty.edu"

VOCAB_URIS = {
    "aat":  "http://vocab.getty.edu/aat/",
    "tgn":  "http://vocab.getty.edu/tgn/",
    "ulan": "http://vocab.getty.edu/ulan/",
    "ia":   "http://vocab.getty.edu/ia/",
    "cona": "http://vocab.getty.edu/cona/",
}

VOCAB_NAMES = {
    "aat":  "Art & Architecture Thesaurus",
    "tgn":  "Thesaurus of Geographic Names",
    "ulan": "Union List of Artist Names",
    "ia":   "Getty Iconography Authority",
    "cona": "Cultural Objects Name Authority",
}

# Vocabs supported by the reconciliation service search
SEARCH_SUPPORTED = {"aat", "tgn", "ulan"}

_HEADERS = {
    "User-Agent": "getty-vocabularies-mcp-server/1.0",
    "Accept":     "application/json",
}

# Classification tags the LOD records use to say what an entry means.
# Getty serves Linked Art with short keys, so a term's role is never visible
# from its key alone — it is spelled out in classified_as.
AAT_PREFERRED_TERM   = "http://vocab.getty.edu/aat/300404670"
AAT_SCOPE_NOTE       = "http://vocab.getty.edu/aat/300435416"
AAT_PREFERRED_PARENT = "http://vocab.getty.edu/aat/300449152"
AAT_PLACE_TYPE       = "http://vocab.getty.edu/aat/300435109"
AAT_ROLE             = "http://vocab.getty.edu/aat/300435108"
AAT_NATIONALITY      = "http://vocab.getty.edu/aat/300379842"
AAT_SEX              = "http://vocab.getty.edu/aat/300055147"
AAT_BIOGRAPHY        = "http://vocab.getty.edu/aat/300080102"
CRM_COORDINATES      = "crm:E47_Spatial_Coordinates"

# Array caps. A single ULAN record carries ~120 names and ~170 KB of JSON,
# and every one of these lists feeds straight into an agent's context.
ALT_LABEL_CAP  = 25
BROADER_CAP    = 25
NARROWER_CAP   = 25
PLACE_TYPE_CAP = 15
ROLE_CAP       = 15
NATIONALITY_CAP = 10
ACTIVE_IN_CAP  = 10
RELATED_CAP    = 10
EQUIVALENT_CAP = 5

# Record fetches allowed for one get_getty_hierarchy call, root included.
HIERARCHY_FETCH_BUDGET = 20

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _detect_vocab(uri: str) -> str:
    """Detect vocabulary key from a URI."""
    for key, prefix in VOCAB_URIS.items():
        if uri.startswith(prefix):
            return key
    return "unknown"


def _normalise_uri(id_or_uri: str, default_vocab: str = "aat") -> tuple[str, str]:
    """
    Given a bare ID or full URI, return (canonical_uri, vocab_key).
    Bare IDs default to the specified vocabulary.
    Normalises to http:// form for consistency (Getty LOD uses http:// in @id values).
    """
    s = id_or_uri.strip()
    if s.startswith("http"):
        uri = s.rstrip("/").replace("https://", "http://")
    else:
        uri = f"http://vocab.getty.edu/{default_vocab}/{s}"
    return uri, _detect_vocab(uri)


def _lod_url(uri: str) -> str:
    """The .json LOD URL for a canonical Getty URI."""
    return uri.replace("http://", "https://").rstrip("/") + ".json"


def _http_get_json(url: str):
    """
    The one function that talks to the network for LOD records.

    Isolated so the test suite can serve fixtures from disk instead: every
    parser test monkeypatches this and nothing else.
    """
    response = requests.get(url, headers=_HEADERS, timeout=15)
    response.raise_for_status()
    return response.json()


def _lod_fetch(uri: str) -> dict:
    """
    Fetch a Getty LOD record as Linked Art JSON by appending .json to the URI.
    Uses https:// for the actual request (Getty redirects http:// to https://).

    Returns {"data": <payload>, "url": <fetched url>} or an error envelope
    carrying "error", "type" and "traceback". The error key is part of the
    tool contract, so failures are never raised to the caller.
    """
    url = _lod_url(uri)
    try:
        return {"data": _http_get_json(url), "url": url}
    except Exception as e:
        return {
            "error": str(e),
            "type": type(e).__name__,
            "traceback": traceback.format_exc(),
        }


def _reconcile_search(query: str, vocab_type: str, limit: int) -> dict:
    """
    POST a keyword query to the Getty reconciliation service.
    vocab_type: one of "/aat", "/tgn", "/ulan", "/all"
    Returns normalised list of {id, label, vocab, uri} or error dict.
    """
    queries_payload = json.dumps({
        "q0": {
            "query": query,
            "type":  vocab_type,
            "limit": limit,
        }
    })
    try:
        response = requests.post(
            RECONCILE_ENDPOINT,
            data={"queries": queries_payload},
            headers={**_HEADERS, "Content-Type": "application/x-www-form-urlencoded"},
            timeout=15,
        )
        response.raise_for_status()
        data = response.json()
        results = []
        for candidate in data.get("q0", {}).get("result", []):
            raw_id = candidate.get("id", "")
            # raw_id is like "aat/300056048" — prepend the base URI
            if raw_id:
                uri = f"http://vocab.getty.edu/{raw_id.lstrip('/')}"
            else:
                uri = ""
            vocab = _detect_vocab(uri)
            getty_id = uri.rstrip("/").split("/")[-1]
            results.append({
                "id":    getty_id,
                "label": candidate.get("name", ""),
                "vocab": vocab,
                "uri":   uri,
                "score": candidate.get("score", None),
            })
        return {"results": results}
    except Exception as e:
        return {
            "error": str(e),
            "type": type(e).__name__,
            "traceback": traceback.format_exc(),
        }


def _excerpt(value, limit: int = 200) -> str:
    """
    A short, safe rendering of a payload for error reporting.

    The records run to ~170 KB; echoing one into an error dict floods an
    agent's context with a payload it cannot use. The result is always
    strictly shorter than `limit`.
    """
    try:
        text = json.dumps(value, ensure_ascii=False)
    except (TypeError, ValueError):
        text = repr(value)
    return text if len(text) < limit else text[: limit - 2] + "…"


def _as_list(value) -> list:
    """The value as a list, for payload keys that may be absent or scalar."""
    if isinstance(value, list):
        return value
    if value is None:
        return []
    return [value]


def _ref_uri(entry) -> str:
    """The canonical http:// URI a reference entry carries, or ""."""
    if isinstance(entry, str):
        value = entry
    elif isinstance(entry, dict):
        value = entry.get("id")
    else:
        return ""
    if not isinstance(value, str) or not value.strip():
        return ""
    return value.strip().rstrip("/").replace("https://", "http://")


def _classification_ids(entry) -> list:
    """
    The classification ids an entry carries, normalized to http:// URIs.

    Getty serves classifications in three shapes — [{"id": …}], a bare
    "http://…" string, and a list holding either — so every classification
    test in this module goes through here. Normalizing is what keeps a bare
    "https://…" string comparable with the tag constants below.
    """
    if isinstance(entry, (list, tuple)):
        ids = []
        for item in entry:
            ids.extend(_classification_ids(item))
        return ids
    if isinstance(entry, str):
        uri = _ref_uri(entry)
        return [uri] if uri else []
    if not isinstance(entry, dict):
        return []
    ids = []
    for item in _as_list(entry.get("classified_as")):
        uri = _ref_uri(item)
        if uri:
            ids.append(uri)
    return ids


def _classified_as(entry, wanted: str) -> bool:
    """Whether an entry is tagged with a given classification."""
    return wanted in _classification_ids(entry)


def _term_entry(entry) -> dict:
    """A {id, label, uri} entry for an inline term reference, or {}."""
    uri = _ref_uri(entry)
    if not uri:
        return {}
    term = {"id": uri.rsplit("/", 1)[-1]}
    label = entry.get("_label") if isinstance(entry, dict) else None
    if isinstance(label, str) and label.strip():
        term["label"] = label.strip()
    term["uri"] = uri
    return term


def _term_entries(entries, cap: int) -> list:
    """{id, label, uri} entries for a reference list, capped."""
    terms = []
    for entry in _as_list(entries):
        term = _term_entry(entry)
        if not term:
            continue
        terms.append(term)
        if len(terms) >= cap:
            break
    return terms


def _classified_terms(node, wanted: str, cap: int) -> list:
    """{id, label, uri} entries for classified_as entries carrying a tag."""
    terms = []
    for entry in _as_list(node.get("classified_as")):
        if not isinstance(entry, dict) or not _classified_as(entry, wanted):
            continue
        term = _term_entry(entry)
        if not term:
            continue
        terms.append(term)
        if len(terms) >= cap:
            break
    return terms


def _names(node) -> list:
    """
    The record's Name entries.

    Identifier entries (the numeric Getty id) and the TGN geometry node are
    not names: including them would put "300177433" in the alt labels.
    """
    names = []
    for entry in _as_list(node.get("identified_by")):
        if not isinstance(entry, dict):
            continue
        if entry.get("type") not in (None, "Name"):
            continue
        content = entry.get("content")
        if isinstance(content, str) and content.strip():
            names.append(entry)
    return names


def _language_tag(entry) -> str:
    """The language subtag of an entry, lowercased; "" when untagged."""
    for lang in _as_list(entry.get("language") if isinstance(entry, dict) else None):
        uri = _ref_uri(lang)
        if uri:
            return uri.rsplit("/", 1)[-1].lower()
    return ""


def _qualified_form(name) -> str:
    """
    Getty's qualified display form of a name, when the payload carries one.

    The preferred name of aat/300177433 has content "frescoes" and the
    qualified heading "frescoes (paintings)" in alternative[0]; AAT guide
    terms keep their angle brackets there too ("<paintings by material or
    technique>"), which is the authorized form catalogers cite.
    """
    for alt in _as_list(name.get("alternative") if isinstance(name, dict) else None):
        if isinstance(alt, dict):
            content = alt.get("content")
            if isinstance(content, str) and content.strip():
                return content.strip()
    return ""


def _preferred_label(node, names) -> tuple:
    """
    The record's display label and the Name entry it came from.

    Preference order: the name tagged "preferred term", in its qualified
    form; the top-level _label; the first name.
    """
    for name in names:
        if _classified_as(name, AAT_PREFERRED_TERM):
            return (_qualified_form(name) or name["content"].strip()), name
    label = node.get("_label")
    if isinstance(label, str) and label.strip():
        return label.strip(), None
    if names:
        return (_qualified_form(names[0]) or names[0]["content"].strip()), names[0]
    return "", None


def _alt_labels(names, chosen, pref_label) -> list:
    """
    Variant names, untagged/English first, other languages tagged.

    A name whose spelling matches the preferred label is not a variant, so
    it is skipped: the preferred label may be the qualified form of one
    name while another name carries the same text verbatim, and repeating
    the heading under altLabels would be noise. Returns the full list
    uncapped; the caller decides what to keep and how many there were.
    """
    preferred, others = [], []
    seen = set()
    for name in names:
        if name is chosen:
            continue
        content = name["content"].strip()
        if not content or content in seen or content == pref_label:
            continue
        seen.add(content)
        tag = _language_tag(name)
        if tag in ("", "en"):
            preferred.append(content)
            continue
        label = f"{content} ({tag})"
        if label in seen:
            continue
        seen.add(label)
        others.append(label)
    return preferred + others


def _scope_note(node) -> tuple:
    """(text, language) of the record's scope note, or ("", "")."""
    candidates = []
    for entry in _as_list(node.get("subject_of")):
        if not isinstance(entry, dict) or not _classified_as(entry, AAT_SCOPE_NOTE):
            continue
        content = entry.get("content")
        if isinstance(content, str) and content.strip():
            candidates.append((_language_tag(entry), content.strip()))
    for tag, text in candidates:
        if tag == "en":
            return text, tag
    if candidates:
        return candidates[0][1], candidates[0][0]
    return "", ""


def _relationship_terms(node) -> list:
    """
    Incoming relationships: other records that relate to this one.

    Getty inlines these as la:related_from_by, each naming its target in
    la:relates_to and its relationship type in classified_as. The type is
    sometimes a labelled entry and sometimes a bare URI, so labels are
    collected first and bare references take their label from a sibling.
    """
    rels = [r for r in _as_list(node.get("la:related_from_by")) if isinstance(r, dict)]
    labels = {}
    for rel in rels:
        for entry in _as_list(rel.get("classified_as")):
            if isinstance(entry, dict):
                uri = _ref_uri(entry)
                label = entry.get("_label")
                if uri and isinstance(label, str) and label.strip():
                    labels.setdefault(uri, label.strip())

    terms = []
    for rel in rels:
        uri = _ref_uri(rel.get("la:relates_to"))
        if not uri:
            continue
        term = {"id": uri.rsplit("/", 1)[-1]}
        relationship_ids = _classification_ids(rel)
        relationship = labels.get(relationship_ids[0], "") if relationship_ids else ""
        if relationship:
            term["relationship"] = relationship
        term["uri"] = uri
        terms.append(term)
        if len(terms) >= RELATED_CAP:
            break
    return terms


def _coordinates(node) -> str:
    """The TGN geometry node's coordinate string, or ""."""
    for entry in _as_list(node.get("identified_by")):
        if isinstance(entry, dict) and entry.get("type") == CRM_COORDINATES:
            value = entry.get("value")
            if isinstance(value, str) and value.strip():
                return value.strip()
    return ""


def _event_year(event) -> int:
    """The year an event's timespan starts, or 0."""
    timespan = event.get("timespan") if isinstance(event, dict) else None
    if not isinstance(timespan, dict):
        return 0
    for key in ("begin_of_the_begin", "end_of_the_end", "begin_of_the_end", "end_of_the_begin"):
        value = timespan.get(key)
        if isinstance(value, str) and len(value) >= 4 and value[:4].isdigit():
            return int(value[:4])
    return 0


def _event_place(event) -> str:
    """The label of the place an event took place at, or ""."""
    places = _as_list(event.get("took_place_at") if isinstance(event, dict) else None)
    for place in places:
        if isinstance(place, dict):
            label = place.get("_label")
            if isinstance(label, str) and label.strip():
                return label.strip()
    return ""


def _active_in(node) -> list:
    """Places the record is recorded as active in, deduped, in payload order."""
    terms, seen = [], set()
    for event in _as_list(node.get("carried_out")):
        for place in _as_list(event.get("took_place_at") if isinstance(event, dict) else None):
            term = _term_entry(place)
            key = term.get("uri") or term.get("label")
            if not key or key in seen:
                continue
            seen.add(key)
            terms.append(term)
            if len(terms) >= ACTIVE_IN_CAP:
                return terms
    return terms


def _biography(node) -> str:
    """The record's brief biography text, or ""."""
    for entry in _as_list(node.get("referred_to_by")):
        if isinstance(entry, dict) and _classified_as(entry, AAT_BIOGRAPHY):
            content = entry.get("content")
            if isinstance(content, str) and content.strip():
                return content.strip()
    return ""


def _equivalent_ids(node) -> list:
    """Equivalents in other vocabularies (Wikidata, LOC), ids only, capped."""
    ids = []
    for entry in _as_list(node.get("equivalent")):
        if isinstance(entry, str):
            value = entry
        elif isinstance(entry, dict):
            value = entry.get("id")
        else:
            continue
        if isinstance(value, str) and value.strip() and value.strip() not in ids:
            ids.append(value.strip())
        if len(ids) >= EQUIVALENT_CAP:
            break
    return ids


def _parse_linked_art(node: dict, uri: str) -> dict:
    """
    Parse one Getty Linked Art record into the structured record dict the
    tools return.

    The endpoint serves a single object with short keys (id, type, _label,
    identified_by, subject_of, …) rather than the RDF graph list it served
    when this server was written. Preference order and per-vocabulary rules
    live in the helpers above; anything a record does not carry is omitted
    rather than emitted as null.
    """
    canon = uri.rstrip("/").replace("https://", "http://")
    node_id = _ref_uri(node)
    if node_id != canon:
        return {
            "error": f"LOD response id does not match the requested URI {canon}",
            "excerpt": _excerpt(node_id or node),
        }

    vocab = _detect_vocab(canon)
    record = {
        "id":        canon.rsplit("/", 1)[-1],
        "uri":       canon,
        "vocab":     vocab,
    }
    if VOCAB_NAMES.get(vocab):
        record["vocabName"] = VOCAB_NAMES[vocab]

    record_type = node.get("type")
    if isinstance(record_type, str) and record_type.strip():
        record["recordType"] = record_type.strip()

    names = _names(node)
    pref_label, chosen = _preferred_label(node, names)
    if pref_label:
        record["prefLabel"] = pref_label
    alt_labels = _alt_labels(names, chosen, pref_label)
    if alt_labels:
        record["altLabels"] = alt_labels[:ALT_LABEL_CAP]
        record["altLabelCount"] = len(alt_labels)

    scope_note, scope_language = _scope_note(node)
    if scope_note:
        record["scopeNote"] = scope_note
        if scope_language:
            record["scopeNoteLanguage"] = scope_language

    # TGN puts parents in part_of and does not inline child places at all;
    # AAT carries both directions inline.
    broader = []
    broader_source = node.get("part_of") if vocab == "tgn" else node.get("broader")
    for entry in _as_list(broader_source):
        term = _term_entry(entry)
        if not term:
            continue
        if vocab == "tgn" and _classified_as(entry, AAT_PREFERRED_PARENT):
            term["preferredParent"] = True
        broader.append(term)
        if len(broader) >= BROADER_CAP:
            break
    if vocab == "tgn":
        record["hierarchyNote"] = (
            "TGN parent places are inlined as broaderTerms (from part_of). "
            "The LOD endpoint does not inline child places, so narrowerTerms "
            "is absent for places — browse the full hierarchy at "
            f"https://vocab.getty.edu/hier/tgn/{record['id']}"
        )
    if broader:
        record["broaderTerms"] = broader

    if vocab != "tgn":
        narrower = _term_entries(node.get("narrower"), NARROWER_CAP)
        if narrower:
            record["narrowerTerms"] = narrower

    related = _relationship_terms(node)
    if related:
        record["relatedTerms"] = related

    if vocab == "tgn":
        place_types = _classified_terms(node, AAT_PLACE_TYPE, PLACE_TYPE_CAP)
        if place_types:
            record["placeTypes"] = place_types
        coordinates = _coordinates(node)
        if coordinates:
            record["coordinates"] = coordinates

    if vocab == "ulan":
        roles = _classified_terms(node, AAT_ROLE, ROLE_CAP)
        if roles:
            record["roles"] = roles
        nationalities = _classified_terms(node, AAT_NATIONALITY, NATIONALITY_CAP)
        if nationalities:
            record["nationalities"] = nationalities
        sex = _classified_terms(node, AAT_SEX, 1)
        if sex:
            record["sex"] = sex[0]
        born, died = node.get("born"), node.get("died")
        birth_year, death_year = _event_year(born), _event_year(died)
        if birth_year:
            record["birthYear"] = birth_year
        if death_year:
            record["deathYear"] = death_year
        birth_place, death_place = _event_place(born), _event_place(died)
        if birth_place:
            record["birthPlace"] = birth_place
        if death_place:
            record["deathPlace"] = death_place
        active_in = _active_in(node)
        if active_in:
            record["activeIn"] = active_in
        biography = _biography(node)
        if biography:
            record["biography"] = biography

    equivalent = _equivalent_ids(node)
    if equivalent:
        record["equivalent"] = equivalent

    return record


# ---------------------------------------------------------------------------
# Tool 1 — Keyword search (AAT, TGN, ULAN via reconciliation service)
# ---------------------------------------------------------------------------

@mcp.tool()
def search_getty(query: str, vocab: str = "all", limit: int = 25) -> dict:
    """
    Search Getty Vocabularies by keyword using the Getty reconciliation service.

    Parameters
    ----------
    query : str
        Search term(s). Partial matches supported.
    vocab : str
        Which vocabulary to search. One of: aat, tgn, ulan, all.
        Default is "all". Note: CONA and IA are not supported by this
        search endpoint — use get_getty_record with a known URI instead.
    limit : int
        Maximum number of results to return. Default 25.

    Returns
    -------
    dict with a 'results' list of {id, label, vocab, uri, score} dicts.
    """
    v = vocab.strip().lower()
    type_map = {
        "aat":  "/aat",
        "tgn":  "/tgn",
        "ulan": "/ulan",
        "all":  "/all",
    }
    if v not in type_map:
        return {
            "error": (
                f"Unknown vocabulary '{vocab}'. "
                "Search supports: aat, tgn, ulan, all. "
                "CONA and IA are not available via keyword search."
            )
        }
    result = _reconcile_search(query, type_map[v], limit)
    if "results" in result:
        result["query"] = query
        result["vocab"] = vocab
    return result


# ---------------------------------------------------------------------------
# Tool 2 — Full record retrieval by URI or ID
# ---------------------------------------------------------------------------

@mcp.tool()
def get_getty_record(id_or_uri: str) -> dict:
    """
    Retrieve the full record for a Getty authority by URI or numeric ID.

    Parameters
    ----------
    id_or_uri : str
        A full Getty URI (e.g. "http://vocab.getty.edu/aat/300056048") or
        a numeric Getty ID. Bare IDs default to AAT — always prefer the
        full URI from search results to avoid ambiguity.

    Returns
    -------
    dict with, when the record carries them: id, uri, vocab, vocabName,
    recordType, prefLabel, altLabels, altLabelCount, scopeNote,
    scopeNoteLanguage, broaderTerms, narrowerTerms, relatedTerms,
    equivalent. TGN records add placeTypes, coordinates and hierarchyNote;
    ULAN records add roles, nationalities, sex, birthYear, deathYear,
    birthPlace, deathPlace, activeIn and biography. Fields a record does not
    carry are omitted.

    Term entries carry id, label and uri — except the relatedTerms entries,
    which carry id, uri and relationship (see below): Getty serves no label
    for the related record itself.

    Notes
    -----
    Getty serves these records as Linked Art. The preferred label is the name
    tagged "preferred term" in its Getty qualified form, so AAT guide terms
    keep their angle brackets ("<paintings by material or technique>").

    relatedTerms lists *incoming* relationships — other records that relate
    to this one, with the relationship label served for them. Getty does not
    serve outgoing relationships in this payload.

    TGN builds broaderTerms from part_of (the parent tagged "preferred
    parent" carries preferredParent: true) and does not inline child places,
    so TGN records carry no narrowerTerms.

    Works with AAT, TGN, and ULAN. CONA and IA records may not return
    full structured data as they are not fully available as LOD.
    """
    uri, _vocab = _normalise_uri(id_or_uri)
    result = _lod_fetch(uri)
    if "error" in result:
        return result
    data = result.get("data")
    if not isinstance(data, dict):
        # Never echo the payload itself: records run to ~170 KB.
        return {
            "error": "Unexpected LOD response format",
            "url": result.get("url", _lod_url(uri)),
            "shape": type(data).__name__,
            "excerpt": _excerpt(data),
        }
    record = _parse_linked_art(data, uri)
    if "error" not in record and not record.get("prefLabel"):
        record["warning"] = (
            f"No preferred label resolved for URI {uri}: the record carries no "
            "preferred-term name, no _label and no name entry. Verify the ID and "
            "the vocabulary prefix, or check that the record is served as "
            "Linked Art."
        )
    return record


# ---------------------------------------------------------------------------
# Tool 3 — Scope note retrieval
# ---------------------------------------------------------------------------

@mcp.tool()
def get_getty_scope_note(id_or_uri: str) -> dict:
    """
    Retrieve the scope note (usage guidance) for a Getty authority record.

    Parameters
    ----------
    id_or_uri : str
        A full Getty URI or numeric Getty ID. Full URI preferred.

    Returns
    -------
    dict with: id, uri, vocab, prefLabel, scopeNote, and scopeNoteLanguage
    when the record tags its note with a language. If no scope note exists,
    a 'message' key explains this. A record that resolved no preferred label
    passes its 'warning' through, so a missing note is not mistaken for a
    wrong URI.

    Notes
    -----
    Scope notes are most consistently present in AAT records.
    """
    record = get_getty_record(id_or_uri)
    if "error" in record:
        return record
    result = {
        "id":        record.get("id", ""),
        "uri":       record.get("uri", ""),
        "vocab":     record.get("vocab", ""),
        "prefLabel": record.get("prefLabel", ""),
    }
    if "scopeNote" in record:
        result["scopeNote"] = record["scopeNote"]
        if "scopeNoteLanguage" in record:
            result["scopeNoteLanguage"] = record["scopeNoteLanguage"]
    else:
        result["message"] = (
            "No scope note found for this record. "
            "Scope notes are most consistently present in AAT records."
        )
    if "warning" in record:
        result["warning"] = record["warning"]
    return result


# ---------------------------------------------------------------------------
# Tool 4 — Hierarchy navigation
# ---------------------------------------------------------------------------

def _walk_fetch(uri: str, walk: dict):
    """
    Fetch and parse one record for the depth-2 hierarchy walk, charging the
    shared fetch budget.

    Returns the parsed record, or None when the budget is spent or the
    record cannot be fetched and parsed — a hierarchy is built from what is
    reachable, so one unreachable term never fails the whole result.
    """
    if walk["remaining"] <= 0:
        walk["stopped"] = True
        return None
    walk["remaining"] -= 1
    walk["fetches"] += 1
    result = _lod_fetch(uri)
    if "error" in result:
        return None
    data = result.get("data")
    if not isinstance(data, dict):
        return None
    record = _parse_linked_art(data, uri)
    return None if "error" in record else record


@mcp.tool()
def get_getty_hierarchy(id_or_uri: str, depth: int = 1) -> dict:
    """
    Retrieve broader and narrower terms for a Getty authority record.

    Parameters
    ----------
    id_or_uri : str
        A full Getty URI or numeric Getty ID.
    depth : int
        Levels of hierarchy to retrieve. 1 = immediate broader/narrower
        only. Maximum 2 for LOD retrieval (deeper levels require
        multiple sequential fetches and are slower). Default 1.

    Returns
    -------
    dict with: id, uri, vocab, vocabName, prefLabel, depth, broaderTerms,
    narrowerTerms, and — at depth 2 — fetches and, when the walk was cut
    short, truncated: true. Term entries carry id, label and uri, and a term
    whose own terms were fetched carries them under the same key. A record
    that resolved no preferred label passes its warning through.

    TGN results also carry hierarchyNote and mark the preferred parent with
    preferredParent: true. Places carry no narrower terms of their own, so
    the narrowerTerms list is always empty for a TGN record.

    Notes
    -----
    Depth 2 fetches one record per related term. The whole operation has a
    single budget of 20 record fetches including the root record, spent in
    payload order — every broader term first, then every narrower term. A
    record already visited is never fetched twice, so a self-referencing or
    cyclic hierarchy terminates instead of looping.

    TGN hierarchy is very deep and the LOD endpoint does not inline child
    places — depth=1 is usually sufficient.
    """
    depth = max(1, min(depth, 2))
    uri, _vocab = _normalise_uri(id_or_uri)

    # The root fetch is part of the budget, so depth=2 can never exceed
    # HIERARCHY_FETCH_BUDGET network calls however large the hierarchy is.
    walk = {"remaining": HIERARCHY_FETCH_BUDGET - 1, "fetches": 1, "stopped": False}
    record = get_getty_record(uri)
    if "error" in record:
        return record

    result = {
        "id":          record.get("id", ""),
        "uri":         record.get("uri", ""),
        "vocab":       record.get("vocab", ""),
        "vocabName":   record.get("vocabName", ""),
        "prefLabel":   record.get("prefLabel", ""),
        "depth":       depth,
        "broaderTerms":  record.get("broaderTerms", []),
        "narrowerTerms": record.get("narrowerTerms", []),
    }
    if "hierarchyNote" in record:
        result["hierarchyNote"] = record["hierarchyNote"]
    if "warning" in record:
        result["warning"] = record["warning"]

    if depth == 2:
        visited = {_ref_uri(uri)}
        for key in ("broaderTerms", "narrowerTerms"):
            for term in result[key]:
                term_uri = _ref_uri(term.get("uri"))
                if not term_uri or term_uri in visited:
                    continue
                visited.add(term_uri)
                deeper = _walk_fetch(term_uri, walk)
                if deeper is None:
                    continue
                children = deeper.get(key)
                if children:
                    term[key] = children
        result["fetches"] = walk["fetches"]
        if walk["stopped"]:
            result["truncated"] = True

    return result


# ---------------------------------------------------------------------------
# Tools 5-7 — Vocabulary-specific convenience search wrappers
# ---------------------------------------------------------------------------

@mcp.tool()
def search_aat(query: str, limit: int = 25) -> dict:
    """
    Search the Getty Art & Architecture Thesaurus (AAT) by keyword.

    Use for: styles, periods, materials, techniques, object types, roles,
    physical attributes, and other concepts used in art and architecture
    cataloging.

    Parameters
    ----------
    query : str
        Search term(s). E.g. "oil painting", "Baroque", "fresco".
    limit : int
        Maximum results to return. Default 25.

    Returns
    -------
    dict with 'results' list of {id, label, vocab, uri, score} dicts.
    """
    return search_getty(query=query, vocab="aat", limit=limit)


@mcp.tool()
def search_ulan(query: str, limit: int = 25) -> dict:
    """
    Search the Getty Union List of Artist Names (ULAN) by keyword.

    Use for: artists, architects, designers, craftspeople, and other
    makers of cultural objects. Full records via get_getty_record include
    birth/death dates and nationality data.

    Parameters
    ----------
    query : str
        Artist or architect name. E.g. "Rembrandt", "Zaha Hadid".
    limit : int
        Maximum results to return. Default 25.

    Returns
    -------
    dict with 'results' list of {id, label, vocab, uri, score} dicts.
    """
    return search_getty(query=query, vocab="ulan", limit=limit)


@mcp.tool()
def search_tgn(query: str, limit: int = 25) -> dict:
    """
    Search the Getty Thesaurus of Geographic Names (TGN) by keyword.

    Use for: place names, historical place names, geographic features.
    Full records via get_getty_record include place type and hierarchy.

    Parameters
    ----------
    query : str
        Place name. E.g. "Florence", "Constantinople", "Nile River".
    limit : int
        Maximum results to return. Default 25.

    Returns
    -------
    dict with 'results' list of {id, label, vocab, uri, score} dicts.
    """
    return search_getty(query=query, vocab="tgn", limit=limit)


# ---------------------------------------------------------------------------
# Resources (convenience wrappers)
# ---------------------------------------------------------------------------

@mcp.resource("getty://aat/search/{query}")
def aat_resource(query: str) -> dict:
    return search_aat(query)

@mcp.resource("getty://ulan/search/{query}")
def ulan_resource(query: str) -> dict:
    return search_ulan(query)

@mcp.resource("getty://tgn/search/{query}")
def tgn_resource(query: str) -> dict:
    return search_tgn(query)

@mcp.resource("getty://record/{uri}")
def record_resource(uri: str) -> dict:
    return get_getty_record(uri)

@mcp.resource("getty://hierarchy/{uri}")
def hierarchy_resource(uri: str) -> dict:
    return get_getty_hierarchy(uri)


# ---------------------------------------------------------------------------
# Server startup
# ---------------------------------------------------------------------------

def start_mcp_server(port: int = None):
    """Starts the Getty MCP server in HTTP/SSE mode or stdio mode."""
    import uvicorn
    if port is not None:
        print(f"Starting getty vocabularies mcp server on HTTP port {port}")
        uvicorn.run(mcp.sse_app(), host="0.0.0.0", port=port)
    else:
        print("Starting getty vocabularies mcp server in stdio mode")
        mcp.run()


if __name__ == "__main__":
    import sys
    cli_port = None
    if len(sys.argv) > 1 and sys.argv[1].isdigit():
        cli_port = int(sys.argv[1])
    start_mcp_server(port=cli_port)
