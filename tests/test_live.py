"""Live tests against the real Getty LOD endpoint.

Skipped unless GETTY_LIVE is set: these are the only tests that touch the
network, and they exist to make served-shape drift visible rather than to
guard day-to-day work.

    GETTY_LIVE=1 python -m unittest discover tests
"""

import json
import os
import unittest

from getty_vocabularies_mcp import server

LIVE = os.environ.get("GETTY_LIVE")


@unittest.skipUnless(LIVE, "set GETTY_LIVE=1 to exercise the real endpoint")
class LiveEndpointTests(unittest.TestCase):

    def test_aat_record(self):
        record = server.get_getty_record("http://vocab.getty.edu/aat/300177433")
        self.assertNotIn("error", record)
        self.assertEqual(record["prefLabel"], "frescoes (paintings)")
        self.assertTrue(record["scopeNote"])
        json.dumps(record)

    def test_tgn_record(self):
        record = server.get_getty_record("http://vocab.getty.edu/tgn/7011179")
        self.assertNotIn("error", record)
        self.assertEqual(record["prefLabel"], "Siena")
        self.assertTrue(record["broaderTerms"])
        self.assertNotIn("narrowerTerms", record)

    def test_ulan_record(self):
        record = server.get_getty_record("http://vocab.getty.edu/ulan/500115493")
        self.assertNotIn("error", record)
        self.assertEqual(record["prefLabel"], "Dürer, Albrecht")
        self.assertEqual(record["birthYear"], 1471)
        self.assertTrue(record["roles"])

    def test_scope_note_and_hierarchy(self):
        note = server.get_getty_scope_note("http://vocab.getty.edu/aat/300177433")
        self.assertNotIn("error", note)
        self.assertTrue(note["scopeNote"])
        hierarchy = server.get_getty_hierarchy("http://vocab.getty.edu/aat/300033701")
        self.assertNotIn("error", hierarchy)
        self.assertGreaterEqual(len(hierarchy["narrowerTerms"]), 10)


if __name__ == "__main__":
    unittest.main()
