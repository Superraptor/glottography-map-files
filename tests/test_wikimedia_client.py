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
        content = '{"license": "CC-BY-4.0"}'
        res = self.client.upload_map(
            page_title=title,
            content_json=content,
            summary="Test summary"
        )
        self.assertEqual(res["result"], "Success")
        self.assertTrue(res["dry_run"])
        self.assertEqual(res["page_title"], title)


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

