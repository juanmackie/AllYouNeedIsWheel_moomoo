"""
Tests for core/evidence_gated_advisor.py — Evidence-Gated Advisor
"""

import unittest
from datetime import datetime

from core.evidence_gated_advisor import (
    EvidenceBlock,
    WheelAdvisorEvidence,
    build_evidence_from_context,
)


class TestEvidenceBlock(unittest.TestCase):
    def test_default_creation(self):
        block = EvidenceBlock()
        self.assertEqual(block.category, "")
        self.assertEqual(block.label, "")
        self.assertEqual(block.content, "")
        self.assertEqual(block.source, "")
        self.assertFalse(block.available)
        self.assertEqual(block.fetched_at, "")

    def test_to_prompt_line_available(self):
        block = EvidenceBlock(
            category="portfolio",
            label="Portfolio",
            content="Account value: $50000.00",
            source="moomoo",
            available=True,
        )
        line = block.to_prompt_line()
        self.assertEqual(line, "[Portfolio] Account value: $50000.00")

    def test_to_prompt_line_unavailable(self):
        block = EvidenceBlock(
            category="portfolio",
            label="Portfolio",
            available=False,
        )
        line = block.to_prompt_line()
        self.assertEqual(line, "[Portfolio] unknown — no data available")


class TestWheelAdvisorEvidence(unittest.TestCase):
    def test_all_blocks_returns_ten(self):
        evidence = WheelAdvisorEvidence()
        blocks = evidence.all_blocks()
        self.assertEqual(len(blocks), 11)

    def test_all_blocks_categories(self):
        evidence = WheelAdvisorEvidence()
        categories = [b.category for b in evidence.all_blocks()]
        expected = [
            "portfolio",
            "positions",
            "opportunities",
            "signal_overlays",
            "macro",
            "vix",
            "iv",
            "earnings",
            "technical",
            "playbook",
            "scan",
        ]
        self.assertEqual(categories, expected)

    def test_to_prompt_contains_required_sections(self):
        evidence = WheelAdvisorEvidence()
        prompt = evidence.to_prompt()
        self.assertIn("=== EVIDENCE FOR WHEEL ADVISOR ===", prompt)
        self.assertIn("IMPORTANT: You may ONLY reference the evidence below.", prompt)
        self.assertIn("If evidence for a category says 'unknown', answer 'unknown'.", prompt)
        self.assertIn("Do not infer or guess missing data.", prompt)

    def test_to_prompt_includes_all_blocks(self):
        evidence = WheelAdvisorEvidence()
        prompt = evidence.to_prompt()
        for block in evidence.all_blocks():
            self.assertIn(block.to_prompt_line(), prompt)

    def test_to_prompt_unknown_for_missing_evidence(self):
        evidence = WheelAdvisorEvidence()
        evidence.portfolio = EvidenceBlock(
            category="portfolio",
            label="Portfolio",
            available=False,
        )
        prompt = evidence.to_prompt()
        self.assertIn("[Portfolio] unknown — no data available", prompt)
        self.assertNotIn("infer", prompt.split("unknown")[0] if "unknown" in prompt else "")

    def test_to_prompt_cites_no_external_data(self):
        evidence = WheelAdvisorEvidence()
        prompt = evidence.to_prompt()
        forbidden = ["news", "rumor", "twitter", "reddit", "cnbc", "bloomberg"]
        for word in forbidden:
            self.assertNotIn(word, prompt.lower())

    def test_evidence_unknown_when_block_missing_content(self):
        evidence = WheelAdvisorEvidence()
        evidence.earnings = EvidenceBlock(
            category="earnings",
            label="Earnings Calendar",
            content="",
            available=False,
        )
        line = evidence.earnings.to_prompt_line()
        self.assertIn("unknown", line)

    def test_all_blocks_default_unavailable(self):
        evidence = WheelAdvisorEvidence()
        for block in evidence.all_blocks():
            self.assertFalse(block.available)
            self.assertEqual(block.content, "")


class TestBuildEvidenceFromContext(unittest.TestCase):
    def test_empty_context_returns_defaults(self):
        evidence = build_evidence_from_context({})
        self.assertIsInstance(evidence, WheelAdvisorEvidence)
        for block in evidence.all_blocks():
            self.assertFalse(block.available)
            self.assertEqual(block.content, "")

    def test_portfolio_evidence(self):
        context = {
            "portfolio": {
                "account_value": 100000.0,
                "available_cash": 25000.0,
            },
        }
        evidence = build_evidence_from_context(context)
        self.assertIn("100,000", evidence.portfolio.content)
        self.assertIn("25,000", evidence.portfolio.content)

    def test_portfolio_missing_fields(self):
        evidence = build_evidence_from_context({"portfolio": {}})
        self.assertFalse(evidence.portfolio.available)
        self.assertEqual(evidence.portfolio.content, "")

    def test_positions_evidence(self):
        context = {
            "positions": [
                {"ticker": "AAPL", "shares": 100, "avg_cost": 150.0, "market_price": 155.0},
                {"ticker": "MSFT", "shares": 50, "avg_cost": 300.0, "market_price": 310.0},
            ],
        }
        evidence = build_evidence_from_context(context)
        self.assertIn("AAPL", evidence.positions.content)
        self.assertIn("MSFT", evidence.positions.content)
        self.assertIn("155.00", evidence.positions.content)

    def test_positions_empty(self):
        evidence = build_evidence_from_context({"positions": []})
        self.assertFalse(evidence.positions.available)
        self.assertEqual(evidence.positions.content, "")

    def test_scored_positions_override_positions(self):
        context = {
            "positions": [
                {"ticker": "AAPL", "shares": 100, "avg_cost": 150.0, "market_price": 155.0},
            ],
            "scored_positions": [
                {
                    "ticker": "AAPL",
                    "option_type": "PUT",
                    "strike": 145.0,
                    "expiration": "20261220",
                    "dte": 21,
                    "stock_price": 150.0,
                    "mid_price": 2.5,
                    "otm_pct": 3.3,
                    "roll_pressure": 30.0,
                    "profit_target_progress": 40.0,
                    "warnings": ["Wide spread"],
                },
            ],
        }
        evidence = build_evidence_from_context(context)
        self.assertIn("scored_positions", evidence.positions.category)
        self.assertIn("roll_pressure", evidence.positions.content)

    def test_opportunities_evidence(self):
        context = {
            "opportunities": [
                {
                    "ticker": "AAPL",
                    "option_type": "PUT",
                    "strike": 145.0,
                    "expiration": "20261220",
                    "premium_per_contract": 250.0,
                    "delta": -0.20,
                    "dte": 21,
                    "annualized_return": 15.5,
                    "score": 85.0,
                },
            ],
        }
        evidence = build_evidence_from_context(context)
        self.assertIn("AAPL", evidence.opportunities.content)
        self.assertIn("85", evidence.opportunities.content)

    def test_opportunities_empty(self):
        evidence = build_evidence_from_context({"opportunities": []})
        self.assertFalse(evidence.opportunities.available)
        self.assertEqual(evidence.opportunities.content, "")

    def test_signal_overlay_evidence(self):
        context = {
            "signal_overlays": [
                {
                    "ticker": "AAPL",
                    "fit": "caution",
                    "warnings": ["Overlay is bearish for a CSP"],
                    "overlay": {
                        "verdict": "conflict",
                        "bias": "bearish",
                        "summary": "capital outflow skew",
                        "capital": {"summary": "capital outflow skew"},
                        "technical": {"summary": "price below 20d mean"},
                        "derivatives": {"summary": "put skew 1.20 PCR"},
                    },
                }
            ],
        }
        evidence = build_evidence_from_context(context)
        self.assertIn("AAPL", evidence.signal_overlays.content)
        self.assertIn("conflict", evidence.signal_overlays.content)
        self.assertIn("capital outflow skew", evidence.signal_overlays.content)

    def test_macro_evidence(self):
        context = {
            "macro": {
                "rate_regime": "tightening",
                "credit_stress": "normal",
                "growth_regime": "stable",
                "inflation_trend": "cooling",
                "yield_curve_slope": "inverted",
                "summary": "Mixed signals",
                "source": "FRED",
            },
        }
        evidence = build_evidence_from_context(context)
        self.assertIn("tightening", evidence.macro.content)
        self.assertIn("stable", evidence.macro.content)
        self.assertEqual(evidence.macro.source, "FRED")

    def test_vix_evidence(self):
        context = {
            "vix": {
                "vix": 18.5,
                "regime": "normal",
                "description": "Normal volatility environment",
                "source": "yahoo",
            },
        }
        evidence = build_evidence_from_context(context)
        self.assertIn("18.5", evidence.vix.content)
        self.assertIn("normal", evidence.vix.content)

    def test_vix_missing_regime(self):
        context = {"vix": {"vix": 25.0}}
        evidence = build_evidence_from_context(context)
        self.assertIn("25.0", evidence.vix.content)

    def test_evidence_missing_all_sections_unknown(self):
        evidence = build_evidence_from_context({})
        for block in evidence.all_blocks():
            self.assertFalse("infer" in block.content.lower() or "guess" in block.content.lower())

    def test_evidence_timestamp_is_isoformat(self):
        context = {"portfolio": {"account_value": 50000, "available_cash": 10000}}
        evidence = build_evidence_from_context(context)
        try:
            datetime.fromisoformat(evidence.portfolio.fetched_at)
        except (ValueError, TypeError):
            self.fail("fetched_at is not ISO format")

    def test_evidence_opportunities_limited_to_five(self):
        many = [
            {
                "ticker": f"T{i}",
                "option_type": "PUT",
                "strike": 100.0,
                "expiration": "20261220",
                "premium_per_contract": 100.0,
                "delta": -0.20,
                "dte": 21,
                "annualized_return": 10.0,
                "score": float(90 - i),
            }
            for i in range(10)
        ]
        context = {"opportunities": many}
        evidence = build_evidence_from_context(context)
        lines = evidence.opportunities.content.strip().split("\n")
        if lines and lines[0]:
            self.assertLessEqual(len(lines), 5)

    def test_evidence_every_claim_maps_to_source(self):
        context = {
            "portfolio": {"account_value": 50000, "available_cash": 10000},
            "positions": [
                {"ticker": "AAPL", "shares": 100, "avg_cost": 150.0, "market_price": 155.0},
            ],
            "macro": {
                "rate_regime": "tightening",
                "credit_stress": "normal",
                "growth_regime": "stable",
                "inflation_trend": "cooling",
                "summary": "Mixed",
                "source": "FRED",
            },
            "vix": {"vix": 15.0, "regime": "normal", "source": "yahoo"},
        }
        evidence = build_evidence_from_context(context)
        blocks = {b.category: b for b in evidence.all_blocks()}

        self.assertEqual(blocks["portfolio"].source, "moomoo")
        self.assertEqual(blocks["positions"].source, "moomoo")
        self.assertEqual(blocks["macro"].source, "FRED")
        self.assertEqual(blocks["vix"].source, "yahoo")

        prompt = evidence.to_prompt()
        self.assertIn("AAPL", prompt)
        self.assertIn("50,000", prompt)
        self.assertIn("tightening", prompt)
        self.assertIn("15.0", prompt)

    def test_no_blank_evidence_lines_in_prompt(self):
        """Every evidence line must be either '[Label] content' or '[Label] unknown'."""
        evidence = build_evidence_from_context({})
        prompt = evidence.to_prompt()
        evidence_section = prompt.split("=== EVIDENCE FOR WHEEL ADVISOR ===")[1]
        for line in evidence_section.split("\n"):
            if (
                not line.strip()
                or line.startswith("Generated")
                or line.startswith("IMPORTANT")
                or line.startswith("Do not")
                or line.startswith("---")
                or line.startswith("Based")
            ):
                continue
            if line.startswith("["):
                self.assertRegex(line, r"^\[.+\] .+", f"Blank evidence line: {line!r}")
            # Lines not starting with [ are section headers or instructions — ok to skip

    def test_unknown_categories_render_unknown_in_prompt(self):
        """When no context is provided, every block should render as unknown."""
        evidence = build_evidence_from_context({})
        prompt = evidence.to_prompt()
        for block in evidence.all_blocks():
            self.assertIn(f"[{block.label}] unknown", prompt)

    def test_available_blocks_render_content(self):
        """When data is provided, available blocks should render actual content."""
        context = {
            "portfolio": {"account_value": 50000, "available_cash": 10000},
            "vix": {"vix": 18.0, "regime": "normal"},
        }
        evidence = build_evidence_from_context(context)
        prompt = evidence.to_prompt()
        self.assertIn("[Portfolio] Account value: $50,000.00", prompt)
        self.assertIn("[VIX Regime] VIX: 18.0", prompt)
        # Blocks without data should still be unknown
        self.assertIn("[IV Environment] unknown", prompt)
        self.assertIn("[Earnings Calendar] unknown", prompt)
        self.assertIn("[Technical Regime] unknown", prompt)
        self.assertIn("[Active Playbook Hypotheses] unknown", prompt)
        self.assertIn("[Recent Scan Summary] unknown", prompt)


if __name__ == "__main__":
    unittest.main()
