"""Offline tests for the Getty Linked Art record parser and the get_getty_* tools.

Every test runs against fixtures on disk through the module's _http_get_json
seam; the suite-level guard in setUpModule fails the run if anything tries to
reach the network.
"""

import json
import pathlib
import re
import unittest
from unittest import mock

import requests

from getty_vocabularies_mcp import server

FIXTURES = pathlib.Path(__file__).parent / "fixtures"

AAT_FRESCOES = "http://vocab.getty.edu/aat/300177433"
AAT_PAINTINGS = "http://vocab.getty.edu/aat/300033701"
TGN_SIENA = "http://vocab.getty.edu/tgn/7011179"
TGN_NUREMBERG = "http://vocab.getty.edu/tgn/7004334"
ULAN_DURER = "http://vocab.getty.edu/ulan/500115493"


class NetworkAccessAttempted(BaseException):
    """
    Raised when a test reaches the network.

    Deliberately a BaseException: the fetch seam catches Exception to build
    its error envelope, and a swallowed network call would look like an
    ordinary failure of the code under test instead of a broken test.
    """


class UnexpectedFetch(BaseException):
    """
    Raised when the code under test fetches a URI the test did not serve.

    Also a BaseException, for the same reason: an AssertionError would be
    caught by the fetch seam and turned into an ordinary error envelope, so
    a walk that went somewhere it should not would pass unnoticed.
    """


def setUpModule():
    """Fail the run on any network access, whatever the test order."""
    for patcher in (
        mock.patch.object(requests, "get", side_effect=NetworkAccessAttempted("requests.get")),
        mock.patch.object(requests, "post", side_effect=NetworkAccessAttempted("requests.post")),
        mock.patch.object(requests.Session, "request",
                          side_effect=NetworkAccessAttempted("requests.Session.request")),
    ):
        patcher.start()
        unittest.addModuleCleanup(patcher.stop)


def load(name):
    """A fixture payload by file stem."""
    with open(FIXTURES / f"{name}.json", encoding="utf-8") as handle:
        return json.load(handle)


class FakeLOD:
    """Serve payloads through the _http_get_json seam and count the calls."""

    def __init__(self, payloads, failing=()):
        self.payloads = {
            uri.rstrip("/").replace("https://", "http://"): payload
            for uri, payload in payloads.items()
        }
        self.failing = {uri.rstrip("/").replace("https://", "http://") for uri in failing}
        self.urls = []

    def __call__(self, url):
        self.urls.append(url)
        uri = url[:-len(".json")] if url.endswith(".json") else url
        uri = uri.replace("https://", "http://").rstrip("/")
        if uri in self.failing:
            # A served-but-broken record, like an HTTP error from Getty.
            raise requests.HTTPError(f"simulated failure for {url}")
        if uri not in self.payloads:
            raise UnexpectedFetch(f"unexpected LOD url: {url}")
        return self.payloads[uri]

    @property
    def calls(self):
        return len(self.urls)


def fixture_lod(*names):
    """A FakeLOD serving the given fixture files, keyed by their record id."""
    payloads = {}
    for name in names:
        payload = load(name)
        payloads[payload["id"]] = payload
    return FakeLOD(payloads)


def full_lod(*names):
    """
    A FakeLOD serving the fixtures plus an empty record for every term they
    reference, so a depth-2 walk can complete without inventing payloads in
    each test.
    """
    payloads = {}
    for name in names:
        payload = load(name)
        payloads[payload["id"]] = payload
        for key in ("broader", "narrower", "part_of"):
            for term in payload.get(key) or []:
                uri = term if isinstance(term, str) else term.get("id")
                if uri:
                    payloads.setdefault(uri, echo_record(uri))
    return FakeLOD(payloads)


def echo_record(uri, broader=(), narrower=()):
    """A minimal Linked Art record for a URI, for hierarchy traversal tests."""
    node_id = uri.rstrip("/")
    terms = lambda uris: [{"id": u, "_label": f"term {u.rsplit('/', 1)[-1]}"} for u in uris]
    payload = {
        "@context": "https://linked.art/ns/v1/linked-art.json",
        "id": node_id,
        "type": "Type",
        "_label": f"term {node_id.rsplit('/', 1)[-1]}",
        "identified_by": [
            {
                "type": "Name",
                "content": f"term {node_id.rsplit('/', 1)[-1]}",
                "classified_as": [{"id": server.AAT_PREFERRED_TERM, "type": "Type"}],
            }
        ],
    }
    if broader:
        payload["broader"] = terms(broader)
    if narrower:
        payload["narrower"] = terms(narrower)
    return payload


def call(tool, *args, lod=None, **kwargs):
    """Run a tool with the fetch seam replaced by a FakeLOD."""
    fake = lod if lod is not None else FakeLOD({})
    with mock.patch.object(server, "_http_get_json", fake):
        result = tool(*args, **kwargs)
    return result, fake


class TrackedValues:
    """Collect every leaf value in a nested result."""

    @staticmethod
    def walk(value):
        if isinstance(value, dict):
            for item in value.values():
                yield from TrackedValues.walk(item)
        elif isinstance(value, list):
            for item in value:
                yield from TrackedValues.walk(item)
        else:
            yield value


class AATRecordTests(unittest.TestCase):
    """AC.1 — AAT record core fields."""

    def setUp(self):
        self.record, _fake = call(server.get_getty_record, AAT_FRESCOES,
                                  lod=fixture_lod("aat-300177433"))

    def test_aat_record_core_fields(self):
        record = self.record
        self.assertNotIn("error", record)
        self.assertEqual(record["prefLabel"], "frescoes (paintings)")
        self.assertEqual(record["recordType"], "Type")
        self.assertTrue(record["altLabels"], "alt labels should not be empty")
        self.assertEqual(record["altLabelCount"], len(record["altLabels"]))
        # The preferred heading is not one of its own variant names.
        self.assertNotIn(record["prefLabel"], record["altLabels"])
        # The numeric identifier is not a name.
        self.assertNotIn("300177433", record["altLabels"])
        self.assertIn("300033701", [term["id"] for term in record["broaderTerms"]])
        self.assertEqual(record["broaderTerms"][0]["label"],
                         "<paintings by material or technique>")
        self.assertTrue(record["scopeNote"].startswith(
            "Paintings made by the technique of fresco painting"))
        self.assertEqual(record["scopeNoteLanguage"], "en")
        self.assertIn("http://www.wikidata.org/entity/Q22669139", record["equivalent"])
        # 300177433 is a leaf: no narrower terms are served.
        self.assertNotIn("narrowerTerms", record)

    def test_aat_related_terms(self):
        """AC.1 — relatedTerms carry the incoming relationship labels."""
        related = self.record["relatedTerms"]
        self.assertTrue(related, "relatedTerms should not be empty")
        self.assertLessEqual(len(related), server.RELATED_CAP)
        for term in related:
            self.assertTrue(term.get("relationship"), f"missing relationship on {term}")
            self.assertTrue(term["uri"].startswith("http://vocab.getty.edu/aat/"))
        self.assertIn("300386202", [term["id"] for term in related])
        labels = {term["id"]: term["relationship"] for term in related}
        # A relationship tagged by bare URI takes its label from a sibling.
        self.assertEqual(labels["300048626"], "made of/require - material")


class AATGuideTermTests(unittest.TestCase):
    """AC.2 — guide terms keep their angle brackets."""

    def setUp(self):
        self.record, self.fake = call(server.get_getty_record, AAT_PAINTINGS,
                                      lod=fixture_lod("aat-300033701"))

    def test_aat_guide_term_and_narrower(self):
        self.assertEqual(self.record["prefLabel"], "<paintings by material or technique>")
        narrower = self.record["narrowerTerms"]
        self.assertGreaterEqual(len(narrower), 10)
        self.assertLessEqual(len(narrower), server.NARROWER_CAP)
        self.assertIn("frescoes (paintings)", [term["label"] for term in narrower])
        self.assertIn("300177433", [term["id"] for term in narrower])
        # This record carries no scope note at all.
        self.assertNotIn("scopeNote", self.record)


class TGNRecordTests(unittest.TestCase):
    """AC.3 — TGN parents come from part_of."""

    def setUp(self):
        self.record, _fake = call(server.get_getty_record, TGN_SIENA,
                                  lod=fixture_lod("tgn-7011179"))

    def test_tgn_part_of_and_place_types(self):
        record = self.record
        self.assertEqual(record["prefLabel"], "Siena")
        self.assertEqual(record["recordType"], "Place")
        broader = {term["id"]: term for term in record["broaderTerms"]}
        self.assertIn("7003168", broader)
        self.assertIn("7024113", broader)
        self.assertTrue(broader["7003168"].get("preferredParent"))
        self.assertNotIn("preferredParent", broader["7024113"])
        self.assertTrue(record["placeTypes"])
        self.assertIn("cities", [term["label"] for term in record["placeTypes"]])
        self.assertEqual(record["coordinates"], "[11.33,43.318]")
        # The LOD endpoint does not inline child places.
        self.assertNotIn("narrowerTerms", record)
        self.assertIn("hier/tgn/7011179", record["hierarchyNote"])

    def test_tgn_preferred_term_selection(self):
        """AC.4 — the preferred-tagged name wins, not the first entry."""
        nuremberg = load("tgn-7004334")
        first = nuremberg["identified_by"][0]
        record, _fake = call(server.get_getty_record, TGN_NUREMBERG,
                             lod=fixture_lod("tgn-7004334"))
        self.assertEqual(record["prefLabel"], "Nürnberg")
        self.assertIn("Nuremberg", record["altLabels"])

        siena = load("tgn-7011179")
        tagged = [
            name for name in siena["identified_by"]
            if any(entry.get("id") == server.AAT_PREFERRED_TERM
                   for entry in name.get("classified_as", []) if isinstance(entry, dict))
        ]
        self.assertEqual(len(tagged), 1)
        # Siena's preferred name is not the first entry in the payload, so the
        # parser cannot be selecting by position.
        self.assertIsNot(tagged[0], siena["identified_by"][0])
        self.assertEqual(self.record["prefLabel"], tagged[0]["content"])
        self.assertTrue(first["content"])


class ULANRecordTests(unittest.TestCase):
    """AC.5 — ULAN life dates, roles, nationalities."""

    def setUp(self):
        self.record, _fake = call(server.get_getty_record, ULAN_DURER,
                                  lod=fixture_lod("ulan-500115493"))

    def test_ulan_life_dates_roles_nationalities(self):
        record = self.record
        self.assertEqual(record["prefLabel"], "Dürer, Albrecht")
        self.assertEqual(record["recordType"], "Person")
        self.assertEqual(record["birthYear"], 1471)
        self.assertEqual(record["deathYear"], 1528)
        self.assertEqual(record["birthPlace"], "Nürnberg")
        self.assertEqual(record["deathPlace"], "Nürnberg")
        self.assertIn("painters (artists)", [term["label"] for term in record["roles"]])
        self.assertIn("German (culture, style, period)",
                      [term["label"] for term in record["nationalities"]])
        self.assertEqual(record["sex"]["label"], "male")
        self.assertIn("German printmaker", record["biography"])
        self.assertIn("Venezia", [term["label"] for term in record["activeIn"]])
        self.assertIn("http://id.loc.gov/authorities/names/n79118011", record["equivalent"])
        # Language variants of the preferred spelling are not alt labels.
        for label in record["altLabels"]:
            self.assertNotEqual(label, record["prefLabel"])
            self.assertFalse(label.startswith(record["prefLabel"] + " ("), label)


class ScopeNoteTests(unittest.TestCase):
    """AC.6 — the scope-note tool."""

    def test_scope_note_tool(self):
        result, _fake = call(server.get_getty_scope_note, AAT_FRESCOES,
                             lod=fixture_lod("aat-300177433"))
        self.assertNotIn("error", result)
        self.assertEqual(result["prefLabel"], "frescoes (paintings)")
        self.assertTrue(result["scopeNote"].startswith(
            "Paintings made by the technique of fresco painting"))
        self.assertEqual(result["scopeNoteLanguage"], "en")

    def test_scope_note_absent_message(self):
        """AC.6 — a record with no scope note explains itself.

        Uses a captured payload (aat/300033701 has no subject_of) rather than
        the synthetic derivative the plan sketched: the real record exercises
        the absence path, so no derived fixture is needed for it.
        """
        result, _fake = call(server.get_getty_scope_note, AAT_PAINTINGS,
                             lod=fixture_lod("aat-300033701"))
        self.assertNotIn("error", result)
        self.assertNotIn("scopeNote", result)
        self.assertIn("message", result)

    def test_scope_note_warning_passthrough(self):
        """AC.6 — a record with no preferred label keeps its warning."""
        result, _fake = call(server.get_getty_scope_note, AAT_FRESCOES,
                             lod=fixture_lod("aat-300177433-no-preflabel"))
        self.assertNotIn("error", result)
        self.assertIn("warning", result)
        self.assertIn("No preferred label resolved", result["warning"])
        # The scope note survives even without a preferred label.
        self.assertTrue(result["scopeNote"])


class HierarchyTests(unittest.TestCase):
    """AC.7 — hierarchy depth, budget and cycle guard."""

    def test_hierarchy_depth1(self):
        result, fake = call(server.get_getty_hierarchy, AAT_PAINTINGS,
                            lod=fixture_lod("aat-300033701"))
        self.assertEqual(result["depth"], 1)
        self.assertEqual(result["prefLabel"], "<paintings by material or technique>")
        self.assertTrue(result["broaderTerms"])
        self.assertGreaterEqual(len(result["narrowerTerms"]), 10)
        self.assertEqual(fake.calls, 1, "depth 1 must not fetch related records")

    def test_hierarchy_depth2_call_cap(self):
        """AC.7 — nesting at depth 2 stays inside the 20-fetch budget."""
        root = load("aat-300033701")
        payloads = {root["id"]: root}
        for term in root["narrower"]:
            uri = term["id"]
            payloads[uri] = echo_record(uri, broader=[root["id"]],
                                        narrower=[uri + "0"])
        for term in root["broader"]:
            uri = term["id"]
            payloads[uri] = echo_record(uri, narrower=[uri + "0"])

        result, fake = call(server.get_getty_hierarchy, AAT_PAINTINGS, 2,
                            lod=FakeLOD(payloads))

        self.assertLessEqual(fake.calls, server.HIERARCHY_FETCH_BUDGET)
        self.assertLessEqual(fake.calls, 20)
        self.assertEqual(result["fetches"], fake.calls)
        self.assertNotIn("truncated", result)
        nested = [term for term in result["narrowerTerms"] if "narrowerTerms" in term]
        self.assertTrue(nested, "children should be nested at depth 2")

    def test_hierarchy_budget_truncates(self):
        """AC.7 — the budget stops the walk and says so."""
        root_uri = "http://vocab.getty.edu/aat/900001100"
        children = [f"http://vocab.getty.edu/aat/9000{i:05d}" for i in range(30)]
        payloads = {root_uri: echo_record(root_uri, narrower=children)}
        for child in children:
            payloads[child] = echo_record(child, broader=[root_uri])

        result, fake = call(server.get_getty_hierarchy, root_uri, 2, lod=FakeLOD(payloads))

        self.assertEqual(fake.calls, 20)
        self.assertEqual(result["fetches"], 20)
        self.assertTrue(result["truncated"])

    def test_hierarchy_cycle_guard(self):
        """AC.7 — a mutually-referencing pair terminates without error."""
        result, fake = call(server.get_getty_hierarchy, "http://vocab.getty.edu/aat/900000001",
                            2, lod=fixture_lod("cycle-a", "cycle-b"))
        self.assertNotIn("error", result)
        self.assertEqual(result["prefLabel"], "cycle a")
        self.assertEqual([term["id"] for term in result["broaderTerms"]], ["900000002"])
        # The root lists itself as a narrower term; the visited set skips it,
        # so exactly the two records are fetched.
        self.assertEqual(fake.calls, 2)
        self.assertNotIn("truncated", result)

    def test_hierarchy_carries_tgn_note(self):
        """AC.3 — the TGN capability limit reaches the hierarchy tool."""
        result, _fake = call(server.get_getty_hierarchy, TGN_SIENA,
                             lod=fixture_lod("tgn-7011179"))
        self.assertIn("hierarchyNote", result)
        self.assertIn("hier/tgn/7011179", result["hierarchyNote"])

    def test_hierarchy_tgn_depth2_has_no_children(self):
        """AC.3 — depth 2 walks TGN parents and still finds no child places."""
        result, fake = call(server.get_getty_hierarchy, TGN_SIENA, 2,
                            lod=full_lod("tgn-7011179"))
        self.assertEqual(result["narrowerTerms"], [])
        self.assertEqual(fake.calls, 3, "root plus the two part_of parents")
        self.assertIn("hierarchyNote", result)
        self.assertNotIn("truncated", result)

    def test_hierarchy_depth_is_clamped(self):
        lod = full_lod("aat-300033701")
        shallow, _fake = call(server.get_getty_hierarchy, AAT_PAINTINGS, 0, lod=lod)
        deep, _fake = call(server.get_getty_hierarchy, AAT_PAINTINGS, 9, lod=lod)
        self.assertEqual(shallow["depth"], 1)
        self.assertEqual(deep["depth"], 2)

    def test_hierarchy_survives_an_unreachable_term(self):
        """AC.7 — one failing record does not fail the whole hierarchy."""
        root_uri = "http://vocab.getty.edu/aat/900002000"
        children = [root_uri + str(index) for index in range(3)]
        payloads = {root_uri: echo_record(root_uri, narrower=children)}
        for child in children[1:]:
            payloads[child] = echo_record(child, broader=[root_uri],
                                          narrower=[child + "x"])
        lod = FakeLOD(payloads, failing=[children[0]])

        result, fake = call(server.get_getty_hierarchy, root_uri, 2, lod=lod)

        self.assertNotIn("error", result)
        self.assertNotIn("truncated", result)
        self.assertEqual(fake.calls, 4)
        self.assertNotIn("narrowerTerms", result["narrowerTerms"][0])
        self.assertIn("narrowerTerms", result["narrowerTerms"][1])

    def test_hierarchy_passes_warning_through(self):
        """A record with no preferred label keeps its warning here too."""
        result, _fake = call(server.get_getty_hierarchy, AAT_FRESCOES,
                             lod=fixture_lod("aat-300177433-no-preflabel"))
        self.assertNotIn("error", result)
        self.assertIn("warning", result)


class PayloadShapeTests(unittest.TestCase):
    """AC.8 — payload shapes that are not the happy path."""

    def test_rejects_non_dict_payload(self):
        payload = [{"@id": "http://vocab.getty.edu/aat/300177433"}]
        result, _fake = call(server.get_getty_record, AAT_FRESCOES,
                             lod=FakeLOD({AAT_FRESCOES: payload}))
        self.assertIn("error", result)
        self.assertEqual(result["shape"], "list")
        self.assertIn("url", result)
        self.assertLess(len(result["excerpt"]), 200)
        self.assertNotIn("data", result)

    def test_parse_mismatched_uri(self):
        payload = load("aat-300177433")
        result, _fake = call(server.get_getty_record, AAT_PAINTINGS,
                             lod=FakeLOD({AAT_PAINTINGS: payload}))
        self.assertIn("error", result)
        self.assertIn("does not match", result["error"])
        self.assertLess(len(result["excerpt"]), 200)

    def test_error_excerpt_stays_short(self):
        """AC.8 — a long payload is never echoed into an error."""
        payload = load("aat-300177433")
        payload.pop("id")
        self.assertGreater(len(json.dumps(payload)), 2000)
        result, _fake = call(server.get_getty_record, AAT_FRESCOES,
                             lod=FakeLOD({AAT_FRESCOES: payload}))
        self.assertIn("error", result)
        self.assertLess(len(result["excerpt"]), 200)

    def test_https_classification_is_normalized(self):
        """AC.8 — an https:// tag with a trailing slash still resolves."""
        payload = load("aat-300177433")
        payload.pop("_label")
        payload["identified_by"][0]["classified_as"] = [
            {"id": "https://vocab.getty.edu/aat/300404670/"}
        ]
        record, _fake = call(server.get_getty_record, AAT_FRESCOES,
                             lod=FakeLOD({AAT_FRESCOES: payload}))
        self.assertNotIn("error", record)
        self.assertEqual(record["prefLabel"], "frescoes (paintings)")

    def test_classified_as_bare_string_tolerated(self):
        payload = load("aat-300177433-bare-classification")
        name = payload["identified_by"][0]
        self.assertIsInstance(name["classified_as"][0], str)
        self.assertTrue(server._classified_as(name, server.AAT_PREFERRED_TERM))

        record, _fake = call(server.get_getty_record, AAT_FRESCOES,
                             lod=FakeLOD({AAT_FRESCOES: payload}))
        self.assertNotIn("error", record)
        self.assertEqual(record["prefLabel"], "frescoes (paintings)")
        self.assertTrue(record["scopeNote"])
        self.assertEqual([term["id"] for term in record["relatedTerms"]],
                         ["300386202", "300048627", "300048626", "300034464"])


class PackagingTests(unittest.TestCase):
    """AC.10 — both dependency declarations pin mcp below 2."""

    def test_dependency_pins_mcp_below_2(self):
        root = pathlib.Path(__file__).parent.parent
        pyproject = (root / "pyproject.toml").read_text(encoding="utf-8")
        requirements = (root / "requirements.txt").read_text(encoding="utf-8")
        for name, text in (("pyproject.toml", pyproject), ("requirements.txt", requirements)):
            self.assertRegex(
                text,
                re.compile(r'mcp\[cli\]\s*>=\s*1\.0\.0\s*,\s*<\s*2'),
                f"{name} must pin mcp[cli]>=1.0.0,<2",
            )


class OutputShapeTests(unittest.TestCase):
    """AC.11 — serializable, no nulls, every array capped."""

    FIXTURES = ("aat-300177433", "aat-300033701", "tgn-7011179", "tgn-7004334",
                "ulan-500115493")

    CAPS = {
        "altLabels": server.ALT_LABEL_CAP,
        "broaderTerms": server.BROADER_CAP,
        "narrowerTerms": server.NARROWER_CAP,
        "placeTypes": server.PLACE_TYPE_CAP,
        "roles": server.ROLE_CAP,
        "nationalities": server.NATIONALITY_CAP,
        "activeIn": server.ACTIVE_IN_CAP,
        "relatedTerms": server.RELATED_CAP,
        "equivalent": server.EQUIVALENT_CAP,
    }

    def test_output_json_serializable_and_no_nulls(self):
        for name in self.FIXTURES:
            payload = load(name)
            lod = FakeLOD({payload["id"]: payload})
            results = [
                call(server.get_getty_record, payload["id"], lod=lod)[0],
                call(server.get_getty_scope_note, payload["id"], lod=lod)[0],
                call(server.get_getty_hierarchy, payload["id"], lod=lod)[0],
            ]
            for result in results:
                self.assertNotIn("error", result, f"{name}: {result.get('error')}")
                json.dumps(result)  # must not raise
                for value in TrackedValues.walk(result):
                    self.assertIsNotNone(value, f"{name}: null value in {result.keys()}")
                    self.assertNotIsInstance(value, float)
                for key, cap in self.CAPS.items():
                    if key in result:
                        self.assertLessEqual(len(result[key]), cap, f"{name}.{key}")

    def test_alt_labels_capped(self):
        """AC.11 — the cap reports what it dropped."""
        names = [{"type": "Name", "content": f"name {index}"} for index in range(40)]
        names[0]["classified_as"] = [{"id": server.AAT_PREFERRED_TERM}]
        payload = {
            "id": AAT_FRESCOES,
            "type": "Type",
            "identified_by": names,
        }
        record, _fake = call(server.get_getty_record, AAT_FRESCOES,
                             lod=FakeLOD({AAT_FRESCOES: payload}))
        self.assertEqual(len(record["altLabels"]), server.ALT_LABEL_CAP)
        self.assertEqual(record["altLabelCount"], 39)
        self.assertEqual(record["prefLabel"], "name 0")


class OfflineGuardTests(unittest.TestCase):
    """AC.9 — the suite-level guard is real."""

    def test_network_is_blocked(self):
        with self.assertRaises(NetworkAccessAttempted):
            requests.get("https://vocab.getty.edu/aat/300177433.json")


if __name__ == "__main__":
    unittest.main()
