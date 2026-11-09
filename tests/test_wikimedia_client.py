"""Unit tests for glottography_to_commons.wikimedia_client"""

import unittest
from glottography_to_commons.config import Config
from glottography_to_commons.wikimedia_client import WikimediaClient


class TestWikimediaClient(unittest.TestCase):

    def setUp(self):
        self.config = Config(dry_run=True)
        self.client = WikimediaClient(self.config)

    def test_dry_run_login(self):
        self.assertTrue(self.client.login())
        self.assertTrue(self.client.is_authenticated)

    def test_dry_run_ensure_category(self):
        self.assertTrue(self.client.ensure_category())

    def test_dry_run_upload_map(self):
        title = "Data:Glottography - English (stan1295) - test.map"
        valid_map = {
            "license": "CC-BY-4.0",
            "description": {"en": "Speaker area map of English (stan1295)."},
            "sources": "Data derived from Zenodo Glottography record 10.5281/zenodo.12345.",
            "mediawikiCategories": [{"name": "Linguistic maps of the English language", "sort": "English"}],
            "data": {
                "type": "FeatureCollection",
                "features": [
                    {
                        "type": "Feature",
                        "geometry": {"type": "Polygon", "coordinates": [[[0.0, 50.0], [1.0, 50.0], [1.0, 51.0], [0.0, 50.0]]]},
                        "properties": {"title": "English"}
                    }
                ]
            }
        }
        import json
        content = json.dumps(valid_map)
        res = self.client.upload_map(
            page_title=title,
            content_json=content,
            summary="Test summary"
        )
        self.assertEqual(res["result"], "Success")
        self.assertTrue(res["dry_run"])
        self.assertEqual(res["page_title"], title)

    def test_validate_map_payload_catches_invalid(self):
        # 1. Invalid namespace
        with self.assertRaises(ValueError):
            self.client.validate_map_payload("File:Test.map", "{}")

        # 2. Contains version
        with self.assertRaises(ValueError):
            self.client.validate_map_payload("Data:Test.map", '{"version": "1.0"}')

        # 3. Coordinate out of bounds
        invalid_coords = {
            "license": "CC-BY-4.0",
            "description": {"en": "Speaker area map of English."},
            "sources": "Data derived from Zenodo DOI 10.5281/zenodo.123.",
            "mediawikiCategories": [{"name": "Linguistic maps of the English language", "sort": "English"}],
            "data": {
                "type": "FeatureCollection",
                "features": [{"type": "Feature", "geometry": {"type": "Point", "coordinates": [250.0, 0.0]}}]
            }
        }
        import json
        with self.assertRaises(ValueError):
            self.client.validate_map_payload("Data:Test.map", json.dumps(invalid_coords))


    def test_build_bot_userpage_wikitext(self):
        wikitext = self.client.build_bot_userpage_wikitext()
        self.assertIn("{{Bot|operator=Superraptor}}", wikitext)
        self.assertIn("'''Operator''':", wikitext)
        self.assertIn("'''Tasks''':", wikitext)
        self.assertIn("'''Operation''':", wikitext)
        self.assertIn("'''When''':", wikitext)
        self.assertIn("'''Maximum edit rate''':", wikitext)
        self.assertIn("'''Language''':", wikitext)
        self.assertIn("Category:Commons bots", wikitext)

    def test_dry_run_ensure_bot_userpage(self):
        self.config.commons_username = "TestBot@BotPassword"
        self.assertTrue(self.client.ensure_bot_userpage())


if __name__ == "__main__":
    unittest.main()

