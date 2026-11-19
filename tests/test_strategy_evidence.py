"""Tests for core.strategy_evidence — honest reporting of ranking evidence."""

import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from core.strategy_evidence import RANKING, load_strategy_evidence, summarize_evidence

NOW = datetime(2026, 11, 19, tzinfo=timezone.utc)


def report(**overrides):
    base = {
        "generated_at": "2026-11-01T00:00:00+00:00",
        "method": {"base_slippage": 0.25, "base_rate": 0.04, "min_oos_cohorts": 8},
        "trade_counts": {"r1": 71, "r2": 63},
        "unresolved": {"r1": {"no_fill": 4, "expired_unknown": 2}},
        "wf_policies_debug": {"tickers": {"hold": ["AAPL", "KO"], "R1_stop2": ["AAPL"]}},
        "walk_forward": {
            "oos_cohorts": 4,
            "oos": {
                "s0.25_r0.04": {
                    "pooled_excess_per_capday": -0.002,
                    "ci_90": [-0.004, 0.001],
                    "n_cohorts": 4,
                    "n_trades": 8,
                    "diff_vs_pool": -0.001,
                    "diff_vs_pool_all": -0.0015,
                    "diff_vs_spy": -0.0005,
                }
            },
        },
        "verdict": {"verdict": "inconclusive", "reasons": ["only 4 out-of-sample resolved cohorts (need at least 8)"]},
        "limitations": ["Short historical window."],
    }
    base.update(overrides)
    return base


class TestSummarizeEvidence(unittest.TestCase):
    def test_no_report_is_explicit(self):
        summary = summarize_evidence(None)
        self.assertEqual(summary["status"], "no_evidence")
        self.assertEqual(summary["ranking"], RANKING)
        self.assertIn("no replay report has been generated", summary["reasons"])
        self.assertIsNone(summary["verdict"])
        self.assertIsNone(summary["outcome"])
        self.assertTrue(summary["simulated"])

    def test_available_report_carries_verdict_sample_and_outcome(self):
        summary = summarize_evidence(report(), source="report.json", now=NOW)
        self.assertEqual(summary["status"], "available")
        self.assertEqual(summary["verdict"], "inconclusive")
        self.assertEqual(summary["sample"]["selected_contracts"], 134)
        self.assertEqual(summary["sample"]["oos_cohorts"], 4)
        self.assertEqual(summary["sample"]["min_oos_cohorts"], 8)
        self.assertEqual(summary["sample"]["unresolved_selections"], 6)
        self.assertEqual(summary["sample"]["distinct_underlyings"], 2)
        self.assertEqual(summary["outcome"]["excess_per_capital_day"], -0.002)
        self.assertEqual(summary["outcome"]["ci_90"], [-0.004, 0.001])
        self.assertEqual(summary["age_days"], 18.0)
        self.assertEqual(summary["limitations"], ["Short historical window."])
        self.assertIn("eligible pool", summary["baseline_note"])

    def test_missing_base_cell_states_why_outcome_is_unknown(self):
        summary = summarize_evidence(report(walk_forward={"oos_cohorts": 0, "oos": {}}))
        self.assertEqual(summary["status"], "available")
        self.assertIsNone(summary["outcome"])
        self.assertTrue(
            any("no base-case out-of-sample cell" in reason for reason in summary["reasons"]), summary["reasons"]
        )

    def test_ranking_and_note_are_fixed_by_contract(self):
        summary = summarize_evidence(report())
        self.assertEqual(summary["ranking"], "capital_velocity_per_day")
        self.assertIn("does not change the ranking", summary["note"])


class TestLoadStrategyEvidence(unittest.TestCase):
    def test_missing_file_path_is_reported(self):
        with tempfile.TemporaryDirectory() as tmp:
            summary = load_strategy_evidence(Path(tmp) / "absent.json")
        self.assertEqual(summary["status"], "no_evidence")
        self.assertIn("no replay report has been generated", summary["reasons"])

    def test_unreadable_file_states_the_reason_not_a_record(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "report.json"
            path.write_text("{not json", encoding="utf-8")
            summary = load_strategy_evidence(path)
        self.assertEqual(summary["status"], "no_evidence")
        self.assertTrue(any("could not be read" in reason for reason in summary["reasons"]))
        self.assertIsNone(summary["outcome"])

    def test_reads_a_report_from_disk(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "report.json"
            path.write_text(json.dumps(report()), encoding="utf-8")
            summary = load_strategy_evidence(path)
        self.assertEqual(summary["status"], "available")
        self.assertEqual(summary["verdict"], "inconclusive")


if __name__ == "__main__":
    unittest.main()
