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
        content = '{"version": 1, "license": "CC-BY-4.0"}'
        res = self.client.upload_map(
            page_title=title,
            content_json=content,
            summary="Test summary"
        )
        self.assertEqual(res["result"], "Success")
        self.assertTrue(res["dry_run"])
        self.assertEqual(res["page_title"], title)


if __name__ == "__main__":
    unittest.main()

