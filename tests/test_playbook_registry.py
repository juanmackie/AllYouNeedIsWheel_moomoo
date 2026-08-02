"""
Tests for core/playbook_registry.py — Wheel Playbook Hypothesis Registry
"""

import json
import unittest
from unittest.mock import MagicMock, patch

from core.playbook_registry import (
    DEFAULT_HYPOTHESES,
    VALID_STATUSES,
    HypothesisRegistry,
    PlaybookHypothesis,
)


class TestValidStatuses(unittest.TestCase):
    def test_all_valid_statuses(self):
        expected = {"exploring", "testing", "validated", "rejected", "monitoring"}
        self.assertEqual(VALID_STATUSES, expected)

    def test_default_hypotheses_ids_are_unique(self):
        ids = [h["hypothesis_id"] for h in DEFAULT_HYPOTHESES]
        self.assertEqual(len(ids), len(set(ids)))

    def test_default_hypotheses_have_valid_statuses(self):
        for h in DEFAULT_HYPOTHESES:
            self.assertIn(h["status"], VALID_STATUSES)

    def test_default_hypotheses_have_required_fields(self):
        for h in DEFAULT_HYPOTHESES:
            self.assertIn("hypothesis_id", h)
            self.assertIn("title", h)
            self.assertIn("description", h)
            self.assertIn("category", h)
            self.assertIn("status", h)
            self.assertIn("tags", h)

    def test_default_hypotheses_count(self):
        self.assertGreaterEqual(len(DEFAULT_HYPOTHESES), 5)


class TestPlaybookHypothesis(unittest.TestCase):
    def test_default_creation(self):
        h = PlaybookHypothesis()
        self.assertEqual(h.hypothesis_id, "")
        self.assertEqual(h.title, "")
        self.assertEqual(h.description, "")
        self.assertEqual(h.category, "general")
        self.assertEqual(h.status, "exploring")
        self.assertEqual(h.tags, [])
        self.assertEqual(h.notes, "")
        self.assertEqual(h.created_at, "")
        self.assertEqual(h.updated_at, "")

    def test_custom_creation(self):
        h = PlaybookHypothesis(
            hypothesis_id="test_001",
            title="Test hypothesis",
            description="A test hypothesis description",
            category="earnings",
            status="testing",
            tags=["csp", "earnings"],
            notes="Some notes",
            created_at="2026-01-01T00:00:00",
            updated_at="2026-01-01T00:00:00",
        )
        self.assertEqual(h.hypothesis_id, "test_001")
        self.assertEqual(h.title, "Test hypothesis")
        self.assertEqual(h.category, "earnings")
        self.assertEqual(h.status, "testing")
        self.assertEqual(h.tags, ["csp", "earnings"])

    def test_invalid_status_still_accepted(self):
        h = PlaybookHypothesis(
            hypothesis_id="bad_status",
            status="invalid_status",
        )
        self.assertEqual(h.status, "invalid_status")
        self.assertNotIn(h.status, VALID_STATUSES)

    def test_to_dict_roundtrip(self):
        h = PlaybookHypothesis(
            hypothesis_id="test_002",
            title="Roundtrip test",
            description="Testing to_dict",
            category="roll",
            status="validated",
            tags=["roll", "dte"],
            notes="Working well",
            created_at="2026-06-01T00:00:00",
            updated_at="2026-06-15T00:00:00",
        )
        d = h.to_dict()
        self.assertEqual(d["hypothesis_id"], "test_002")
        self.assertEqual(d["status"], "validated")
        self.assertEqual(d["tags"], ["roll", "dte"])

    def test_empty_tags(self):
        h = PlaybookHypothesis(hypothesis_id="no_tags", tags=[])
        self.assertEqual(h.tags, [])


class TestHypothesisRegistryConnect(unittest.TestCase):
    def setUp(self):
        self.mock_db = MagicMock()
        self.mock_db.db_path = ":memory:"
        self.registry = HypothesisRegistry(self.mock_db)

    def tearDown(self):
        self.registry.close()

    @patch("db.sqlite_pool.pooled_connection")
    def test_ensure_defaults_inserts_missing(self, mock_pool):
        mock_conn = MagicMock()
        mock_conn.execute.return_value.fetchone.return_value = None
        mock_pool.return_value.__enter__.return_value = mock_conn

        self.registry._ensure_defaults()
        self.assertGreater(mock_conn.execute.call_count, 1)

    @patch("db.sqlite_pool.pooled_connection")
    def test_ensure_defaults_skips_existing(self, mock_pool):
        mock_conn = MagicMock()
        mock_conn.execute.return_value.fetchone.return_value = [1]
        mock_pool.return_value.__enter__.return_value = mock_conn

        self.registry._ensure_defaults()
        self.assertEqual(
            mock_conn.execute.return_value.fetchone.call_count,
            len(DEFAULT_HYPOTHESES),
        )

    @patch("db.sqlite_pool.pooled_connection")
    def test_list_all_empty(self, mock_pool):
        mock_conn = MagicMock()
        mock_conn.execute.return_value.fetchall.return_value = []
        mock_pool.return_value.__enter__.return_value = mock_conn

        result = self.registry.list_all()
        self.assertEqual(result, [])

    @patch("db.sqlite_pool.pooled_connection")
    def test_list_all_with_status_filter(self, mock_pool):
        mock_conn = MagicMock()
        mock_conn.execute.return_value.fetchall.return_value = []
        mock_pool.return_value.__enter__.return_value = mock_conn

        result = self.registry.list_all(status="testing")
        self.assertEqual(result, [])

    @patch("db.sqlite_pool.pooled_connection")
    def test_get_missing(self, mock_pool):
        mock_conn = MagicMock()
        mock_conn.execute.return_value.fetchone.return_value = None
        mock_pool.return_value.__enter__.return_value = mock_conn

        result = self.registry.get("nonexistent")
        self.assertIsNone(result)

    @patch("db.sqlite_pool.pooled_connection")
    def test_create_hypothesis(self, mock_pool):
        mock_conn = MagicMock()
        mock_pool.return_value.__enter__.return_value = mock_conn

        h = PlaybookHypothesis(
            hypothesis_id="new_test",
            title="New test",
            description="A new test hypothesis",
            category="iv",
            status="exploring",
            tags=["iv", "premium"],
            notes="",
        )
        result = self.registry.create(h)
        self.assertTrue(result)
        mock_conn.execute.assert_called()

    @patch("db.sqlite_pool.pooled_connection")
    def test_create_with_invalid_status_normalizes(self, mock_pool):
        mock_conn = MagicMock()
        mock_pool.return_value.__enter__.return_value = mock_conn

        h = PlaybookHypothesis(
            hypothesis_id="bad_status_test",
            title="Bad status",
            description="Testing invalid status normalization",
            status="invalid",
        )
        result = self.registry.create(h)
        self.assertTrue(result)
        call_args = mock_conn.execute.call_args[0][1]
        self.assertEqual(call_args[4], "exploring")

    @patch("db.sqlite_pool.pooled_connection")
    def test_update_status_valid(self, mock_pool):
        mock_conn = MagicMock()
        mock_conn.execute.return_value.rowcount = 1
        mock_pool.return_value.__enter__.return_value = mock_conn

        result = self.registry.update_status("test_hyp", "validated")
        self.assertTrue(result)
        mock_conn.execute.assert_called_once()

    @patch("db.sqlite_pool.pooled_connection")
    def test_update_status_invalid(self, mock_pool):
        mock_conn = MagicMock()
        mock_pool.return_value.__enter__.return_value = mock_conn

        result = self.registry.update_status("test_hyp", "not_a_status")
        self.assertFalse(result)
        mock_conn.execute.assert_not_called()

    @patch("db.sqlite_pool.pooled_connection")
    def test_update_status_lifecycle(self, mock_pool):
        mock_conn = MagicMock()
        mock_conn.execute.return_value.rowcount = 1
        mock_pool.return_value.__enter__.return_value = mock_conn

        for status in ["exploring", "testing", "validated"]:
            result = self.registry.update_status("test_hyp", status)
            self.assertTrue(result)

    @patch("db.sqlite_pool.pooled_connection")
    def test_update_notes(self, mock_pool):
        mock_conn = MagicMock()
        mock_conn.execute.return_value.rowcount = 1
        mock_pool.return_value.__enter__.return_value = mock_conn

        result = self.registry.update_notes("test_hyp", "Updated notes")
        self.assertTrue(result)
        mock_conn.execute.assert_called_once()

    @patch("db.sqlite_pool.pooled_connection")
    def test_delete_hypothesis(self, mock_pool):
        mock_conn = MagicMock()
        mock_conn.execute.return_value.rowcount = 1
        mock_pool.return_value.__enter__.return_value = mock_conn

        result = self.registry.delete("test_hyp")
        self.assertTrue(result)
        mock_conn.execute.assert_called_once()

    @patch("db.sqlite_pool.pooled_connection")
    def test_create_duplicate_id(self, mock_pool):
        mock_conn = MagicMock()
        mock_conn.execute.side_effect = Exception("UNIQUE constraint failed")
        mock_pool.return_value.__enter__.return_value = mock_conn

        h = PlaybookHypothesis(
            hypothesis_id="duplicate",
            title="Duplicate",
            description="Should fail",
        )
        result = self.registry.create(h)
        self.assertFalse(result)


class TestHypothesisRegistryWithRows(unittest.TestCase):
    def setUp(self):
        self.mock_db = MagicMock()
        self.mock_db.db_path = ":memory:"
        self.registry = HypothesisRegistry(self.mock_db)

    def tearDown(self):
        self.registry.close()

    @patch("db.sqlite_pool.pooled_connection")
    def test_list_all_parses_tags_json(self, mock_pool):
        mock_conn = MagicMock()
        mock_row = {
            "id": 1,
            "hypothesis_id": "test_001",
            "title": "Test",
            "description": "Desc",
            "category": "general",
            "status": "exploring",
            "tags_json": json.dumps(["tag1", "tag2"]),
            "notes": "",
            "created_at": "2026-01-01",
            "updated_at": "2026-01-01",
        }
        mock_conn.execute.return_value.fetchall.return_value = [mock_row]
        mock_pool.return_value.__enter__.return_value = mock_conn

        result = self.registry.list_all()
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["tags"], ["tag1", "tag2"])
        self.assertNotIn("tags_json", result[0])

    @patch("db.sqlite_pool.pooled_connection")
    def test_list_all_by_status(self, mock_pool):
        mock_conn = MagicMock()
        mock_conn.execute.return_value.fetchall.return_value = []
        mock_pool.return_value.__enter__.return_value = mock_conn

        result = self.registry.list_all(status="testing")
        mock_conn.execute.assert_called_with(
            "SELECT * FROM playbook_hypotheses WHERE status = ? ORDER BY updated_at DESC",
            ("testing",),
        )
        self.assertEqual(result, [])

    @patch("db.sqlite_pool.pooled_connection")
    def test_get_parses_tags(self, mock_pool):
        mock_conn = MagicMock()
        mock_row = {
            "id": 1,
            "hypothesis_id": "test_001",
            "title": "Test",
            "description": "Desc",
            "category": "general",
            "status": "exploring",
            "tags_json": json.dumps(["csp", "earnings"]),
            "notes": "",
            "created_at": "2026-01-01",
            "updated_at": "2026-01-01",
        }
        mock_conn.execute.return_value.fetchone.return_value = mock_row
        mock_pool.return_value.__enter__.return_value = mock_conn

        result = self.registry.get("test_001")
        self.assertEqual(result["tags"], ["csp", "earnings"])

    @patch("db.sqlite_pool.pooled_connection")
    def test_get_with_bad_tags_json(self, mock_pool):
        mock_conn = MagicMock()
        mock_row = {
            "id": 1,
            "hypothesis_id": "bad_tags",
            "title": "Bad Tags",
            "description": "Desc",
            "category": "general",
            "status": "exploring",
            "tags_json": "not valid json{{{",
            "notes": "",
            "created_at": "2026-01-01",
            "updated_at": "2026-01-01",
        }
        mock_conn.execute.return_value.fetchone.return_value = mock_row
        mock_pool.return_value.__enter__.return_value = mock_conn

        result = self.registry.get("bad_tags")
        self.assertEqual(result["tags"], [])


class TestDefaultHypothesesContent(unittest.TestCase):
    def test_earnings_avoid_5d(self):
        hyp = next(h for h in DEFAULT_HYPOTHESES if h["hypothesis_id"] == "earnings_avoid_5d")
        self.assertEqual(hyp["category"], "earnings")
        self.assertIn("csp", hyp["tags"])
        self.assertIn("earnings", hyp["tags"])

    def test_prefer_20_35_dte(self):
        hyp = next(h for h in DEFAULT_HYPOTHESES if h["hypothesis_id"] == "prefer_20_35_dte")
        self.assertEqual(hyp["category"], "dte")
        self.assertEqual(hyp["status"], "testing")
        self.assertIn("csp", hyp["tags"])
        self.assertIn("dte", hyp["tags"])

    def test_roll_at_21_dte(self):
        hyp = next(h for h in DEFAULT_HYPOTHESES if h["hypothesis_id"] == "roll_at_21_dte")
        self.assertEqual(hyp["category"], "roll")
        self.assertEqual(hyp["status"], "exploring")

    def test_close_50pct_profit(self):
        hyp = next(h for h in DEFAULT_HYPOTHESES if h["hypothesis_id"] == "close_50pct_profit")
        self.assertEqual(hyp["category"], "exit")
        self.assertEqual(hyp["status"], "testing")

    def test_iv_rank_above_40(self):
        hyp = next(h for h in DEFAULT_HYPOTHESES if h["hypothesis_id"] == "iv_rank_above_40")
        self.assertEqual(hyp["category"], "iv")
        self.assertEqual(hyp["status"], "exploring")


if __name__ == "__main__":
    unittest.main()
