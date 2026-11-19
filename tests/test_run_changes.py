"""Tests for core.run_changes — "what changed since last visit" diffing."""

import unittest

from core.action_plan import build_next_actions
from core.run_changes import diff_actions


def _roll(ticker, strike, expiration, verdict, **kwargs):
    row = {
        "ticker": ticker,
        "option_type": "CALL",
        "strike": strike,
        "expiration": expiration,
        "dte": 10,
        "exit_verdict": verdict,
        "ask": 0.3,
        "quantity": 1,
    }
    row.update(kwargs)
    return row


class TestActionIdentity(unittest.TestCase):
    def test_rank_is_not_part_of_identity_or_revision(self):
        first = build_next_actions(roll_decisions=[_roll("AAPL", 100, "20261218", "HOLD")])
        # An unrelated urgent close appears and outranks the HOLD.
        second = build_next_actions(
            roll_decisions=[
                _roll("AAPL", 100, "20261218", "HOLD"),
                _roll("MSFT", 200, "20261218", "CLOSE", dte=3),
            ]
        )
        self.assertEqual(first[0]["action_id"], "AAPL:CALL:100:20261218")
        self.assertEqual(first[0]["rank"], 1)
        held = next(action for action in second if action["underlying"] == "AAPL")
        self.assertEqual(held["action_id"], first[0]["action_id"])
        self.assertEqual(held["revision"], first[0]["revision"])
        self.assertEqual(held["rank"], 2)

    def test_material_change_changes_the_revision(self):
        hold = build_next_actions(roll_decisions=[_roll("AAPL", 100, "20261218", "HOLD")])[0]
        close = build_next_actions(roll_decisions=[_roll("AAPL", 100, "20261218", "CLOSE")])[0]
        self.assertEqual(hold["action_id"], close["action_id"])
        self.assertNotEqual(hold["revision"], close["revision"])

    def test_cash_allocation_has_a_stable_subject_and_next_session_deadline(self):
        plan = {"lines": [{"ticker": "ABC", "option_type": "PUT", "strike": 10, "expiration": "20261023", "bid": 0.5}]}
        action = build_next_actions(deployment_plan=plan)[0]
        self.assertEqual(action["action_id"], "CASH")
        self.assertEqual(action["deadline"], "next_session")
        self.assertEqual(action["urgency"], "routine")

    def test_held_contract_deadline_is_the_expiry(self):
        action = build_next_actions(roll_decisions=[_roll("AAPL", 100, "20261218", "HOLD")])[0]
        self.assertEqual(action["deadline"], "20261218")


class TestDiffActions(unittest.TestCase):
    def test_reordering_alone_creates_no_change(self):
        before = build_next_actions(roll_decisions=[_roll("AAPL", 100, "20261218", "HOLD")])
        after = build_next_actions(
            roll_decisions=[
                _roll("AAPL", 100, "20261218", "HOLD"),
                _roll("MSFT", 200, "20261218", "CLOSE", dte=3),
            ]
        )
        diff = diff_actions(before, after)
        self.assertEqual(len(diff["new"]), 1)  # MSFT is genuinely new
        self.assertEqual(diff["new"][0]["underlying"], "MSFT")
        self.assertEqual(diff["changed"], [])
        self.assertEqual(diff["resolved"], [])
        self.assertEqual(diff["unchanged_count"], 1)

        # Same subjects, only rank order changes: nothing is new or changed.
        reordered = build_next_actions(
            roll_decisions=[
                _roll("MSFT", 200, "20261218", "CLOSE", dte=3),
                _roll("AAPL", 100, "20261218", "HOLD"),
            ]
        )
        reverse = build_next_actions(
            roll_decisions=[
                _roll("AAPL", 100, "20261218", "HOLD", dte=20),
                _roll("MSFT", 200, "20261218", "CLOSE", dte=3),
            ]
        )
        self.assertEqual(diff_actions(reordered, reverse)["material_change_count"], 0)

    def test_verdict_change_is_reported_with_field_labels(self):
        before = build_next_actions(roll_decisions=[_roll("AAPL", 100, "20261218", "HOLD")])
        after = build_next_actions(roll_decisions=[_roll("AAPL", 100, "20261218", "CLOSE")])
        diff = diff_actions(before, after)
        self.assertEqual(diff["new"], [])
        self.assertEqual(len(diff["changed"]), 1)
        entry = diff["changed"][0]
        self.assertEqual(entry["action_id"], "AAPL:CALL:100:20261218")
        self.assertIn("verdict", entry["fields"])
        self.assertIn("action changed", entry["labels"])
        self.assertEqual(entry["previous_verdict"], "HOLD")
        self.assertEqual(diff["unchanged_count"], 0)

    def test_quantity_change_is_material(self):
        plan_one = {
            "lines": [
                {
                    "ticker": "ABC",
                    "option_type": "PUT",
                    "strike": 10,
                    "expiration": "20261023",
                    "bid": 0.5,
                    "deployment_contracts": 1,
                }
            ]
        }
        plan_three = {
            "lines": [
                {
                    "ticker": "ABC",
                    "option_type": "PUT",
                    "strike": 10,
                    "expiration": "20261023",
                    "bid": 0.5,
                    "deployment_contracts": 3,
                }
            ]
        }
        diff = diff_actions(
            build_next_actions(deployment_plan=plan_one), build_next_actions(deployment_plan=plan_three)
        )
        self.assertEqual(len(diff["changed"]), 1)
        self.assertIn("quantity", diff["changed"][0]["fields"])

    def test_resolved_subject_is_reported(self):
        before = build_next_actions(roll_decisions=[_roll("AAPL", 100, "20261218", "CLOSE")])
        after = build_next_actions(roll_decisions=[])
        diff = diff_actions(before, after)
        self.assertEqual(len(diff["resolved"]), 1)
        self.assertEqual(diff["resolved"][0]["underlying"], "AAPL")
        self.assertEqual(diff["material_change_count"], 1)

    def test_empty_previous_is_first_publication(self):
        after = build_next_actions(roll_decisions=[_roll("AAPL", 100, "20261218", "HOLD")])
        diff = diff_actions([], after)
        self.assertEqual(len(diff["new"]), 1)
        self.assertEqual(diff["previous_action_count"], 0)

    def test_a_previous_publication_without_revisions_is_not_comparable(self):
        legacy = [
            {
                "action_id": "AAPL:CALL:100:20261218",
                "rank": 1,
                "underlying": "AAPL",
                "verdict": "HOLD",
            }
        ]
        current = build_next_actions(roll_decisions=[_roll("AAPL", 100, "20261218", "HOLD")])
        diff = diff_actions(legacy, current)
        self.assertFalse(diff["comparable"])
        self.assertIn("predates change tracking", diff["reason"])
        self.assertEqual(diff["changed"], [])
        self.assertEqual(diff["new"], [])

    def test_comparable_flag_is_true_for_tracked_publications(self):
        current = build_next_actions(roll_decisions=[_roll("AAPL", 100, "20261218", "HOLD")])
        self.assertTrue(diff_actions(current, current)["comparable"])


if __name__ == "__main__":
    unittest.main()
