import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from physical_data.acquisition import SnapshotStore, STAC_SEARCH, collect_stac, eurostat_url, stac_query, validate_public_url


def feature(identifier):
    return {"type": "Feature", "collection": "sentinel-2-l2a", "id": identifier,
        "assets": {"red": {"raster:bands": [{"scale": .0001, "offset": -.1, "nodata": 0}]}}}


def page(items=(), links=()):
    return {"type": "FeatureCollection", "features": list(items), "links": list(links)}


class AcquisitionTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.query = stac_query([9.9, 56, 10, 56.1], "2024-04-01", "2024-04-02")

    def store(self, responses):
        self.calls = []
        def transport(method, url, body):
            self.calls.append((method, url, body))
            return json.dumps(responses[len(self.calls) - 1]).encode()
        return SnapshotStore(self.temp.name, transport=transport, clock=lambda: "2024-04-03T00:00:00Z", data_kind="synthetic")

    def test_get_pagination_dedup_metadata_and_offline_replay(self):
        a, b = feature("a"), feature("b")
        store = self.store([page([a], [{"rel": "next", "href": "?page=2"}]), page([a, b])])
        result = collect_stac(store, self.query)
        self.assertEqual(result["page_count"], 2)
        self.assertEqual(len(result["features"]), 2)
        self.assertEqual(self.calls[1], ("GET", STAC_SEARCH + "?page=2", None))
        self.assertEqual(result["features"][0]["assets"], a["assets"])
        offline = SnapshotStore(self.temp.name, offline=True, data_kind="synthetic")
        self.assertEqual(collect_stac(offline, self.query), result)

    def test_post_pagination_merge_preserves_original_query(self):
        store = self.store([page([feature("a")], [{"rel": "next", "href": STAC_SEARCH, "method": "POST", "body": {"token": "page-two"}, "merge": True}]), page([feature("b")])])
        collect_stac(store, self.query)
        self.assertEqual(self.calls[1][2], {**self.query, "token": "page-two"})
        self.assertNotIn("token", self.query)

    def test_post_pagination_without_merge_replaces_body(self):
        store = self.store([page([], [{"rel": "next", "href": STAC_SEARCH, "method": "POST", "body": {"page": 2}}]), page()])
        collect_stac(store, self.query)
        self.assertEqual(self.calls[1][2], {"page": 2})

    def test_query_is_in_cache_identity(self):
        store = self.store([page(), page()])
        store.fetch(STAC_SEARCH, method="POST", body=self.query)
        store.fetch(STAC_SEARCH, method="POST", body={**self.query, "limit": 1})
        self.assertEqual(len(list(Path(self.temp.name).iterdir())), 2)

    def test_cache_integrity_failure(self):
        store = self.store([page()]); collect_stac(store, self.query)
        response = next(Path(self.temp.name).rglob("response.json"))
        response.write_text("{}")
        with self.assertRaisesRegex(ValueError, "integrity"):
            collect_stac(SnapshotStore(self.temp.name, offline=True, data_kind="synthetic"), self.query)

    def test_missing_snapshot_offline_fails(self):
        with self.assertRaises(FileNotFoundError):
            collect_stac(SnapshotStore(self.temp.name, offline=True), self.query)

    def test_incomplete_cache_fails_closed(self):
        store = self.store([page()]); collect_stac(store, self.query)
        next(Path(self.temp.name).rglob("manifest.json")).unlink()
        with self.assertRaisesRegex(ValueError, "Incomplete"):
            collect_stac(store, self.query)

    def test_page_limit_is_an_error_not_partial_success(self):
        store = self.store([page([], [{"rel": "next", "href": "?page=2"}])])
        with self.assertRaisesRegex(ValueError, "page limit"):
            collect_stac(store, self.query, max_pages=1)

    def test_loop_and_cross_host_rejected(self):
        store = self.store([page([], [{"rel": "next", "href": STAC_SEARCH, "method": "POST", "body": self.query}])])
        with self.assertRaisesRegex(ValueError, "loop"):
            collect_stac(store, self.query)
        with TemporaryDirectory() as temp:
            store = SnapshotStore(temp, transport=lambda *_: json.dumps(page([], [{"rel": "next", "href": "https://example.com/private"}])).encode())
            with self.assertRaisesRegex(ValueError, "Cross-host"):
                collect_stac(store, self.query)

    def test_conflicting_duplicate_item_rejected(self):
        first = feature("a"); second = {**first, "extra": "changed"}
        store = self.store([page([first, second])])
        with self.assertRaisesRegex(ValueError, "Conflicting"):
            collect_stac(store, self.query)

    def test_bad_bbox_dates_and_url(self):
        with self.assertRaises(ValueError):
            stac_query([20, 56, 10, 56.1], "2024-04-01", "2024-04-02")
        with self.assertRaises(ValueError):
            stac_query([9.9, 56, 10, 56.1], "2024-04-02", "2024-04-01")
        for url in ["http://ec.europa.eu/foo", "https://ec.europa.eu.evil.test/", "https://user:password@ec.europa.eu/foo", "file:///tmp/data"]:
            with self.assertRaises(ValueError):
                validate_public_url(url)
        self.assertIn("geo=DK", eurostat_url("DK", "C1100", 2023))

    def test_header_dependent_pagination_is_explicitly_unsupported(self):
        store = self.store([page([], [{"rel": "next", "href": "?page=2", "headers": {"x-page": "2"}}])])
        with self.assertRaisesRegex(ValueError, "Header-dependent"):
            collect_stac(store, self.query)
