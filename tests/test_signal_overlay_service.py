import unittest
from unittest.mock import MagicMock

import pandas as pd

from api.services.signal_overlay_service import SignalOverlayService, apply_signal_overlay


class TestSignalOverlayService(unittest.TestCase):
    def test_apply_signal_overlay_adds_directional_warning_without_changing_core_fields(self):
        signal = {
            "ticker": "AAPL",
            "signal_type": "csp",
            "option_type": "PUT",
            "score": 84.0,
            "warnings": ["Baseline warning"],
        }
        overlay = {
            "verdict": "confirming",
            "bias": "bearish",
            "summary": "capital outflow skew",
            "capital": {"summary": "capital outflow skew"},
            "technical": {"summary": "price below 20d mean"},
            "derivatives": {"summary": "balanced options flow"},
        }

        enriched = apply_signal_overlay(signal, overlay)

        self.assertEqual(enriched["score"], 84.0)
        self.assertEqual(enriched["signal_overlay_fit"], "caution")
        self.assertIn("signal_overlay", enriched)
        self.assertIn("bearish for a CSP", " | ".join(enriched["warnings"]))

    def test_get_overlays_builds_multi_dimensional_payload(self):
        service = SignalOverlayService(config_provider={"host": "127.0.0.1", "port": 11111})
        mock_conn = MagicMock()
        mock_conn.is_connected.return_value = True
        mock_conn.get_capital_distribution.return_value = (
            0,
            pd.DataFrame([
                {
                    "capital_in_super": 1200,
                    "capital_in_big": 500,
                    "capital_in_mid": 100,
                    "capital_in_small": 50,
                    "capital_out_super": 120,
                    "capital_out_big": 40,
                    "capital_out_mid": 20,
                    "capital_out_small": 10,
                    "update_time": "2026-06-04 10:00:00",
                }
            ]),
        )
        mock_conn.get_capital_flow.return_value = (
            0,
            pd.DataFrame([
                {"in_flow": 100, "main_in_flow": 75},
                {"in_flow": 80, "main_in_flow": 50},
            ]),
        )
        mock_conn.get_market_snapshot.return_value = (
            0,
            pd.DataFrame([
                {
                    "enable_short_sell": True,
                    "short_sell_rate": 2.5,
                    "short_available_volume": 22000,
                }
            ]),
        )
        mock_conn.get_history_kline.return_value = (
            0,
            pd.DataFrame(
                {
                    "close": [100 + i for i in range(60)],
                    "high": [101 + i for i in range(60)],
                    "low": [99 + i for i in range(60)],
                    "volume": [1000 + i * 10 for i in range(60)],
                }
            ),
            None,
        )
        mock_conn.get_option_expiration_dates.return_value = (
            0,
            pd.DataFrame([
                {"expiration_date": "20260619"},
                {"expiration_date": "20260717"},
            ]),
        )
        mock_conn.get_option_chain.side_effect = lambda ticker, exp, right: (
            0,
            {
                "options": [
                    {
                        "strike": 110,
                        "bid": 1.0,
                        "ask": 1.2,
                        "last": 1.1,
                        "volume": 120,
                        "open_interest": 200,
                        "implied_volatility": 0.25,
                    }
                ]
            },
        )
        service._ensure_connection = MagicMock(return_value=mock_conn)

        result = service.get_overlays(["AAPL"])

        self.assertTrue(result["source_available"])
        self.assertIn("AAPL", result["overlays"])
        overlay = result["overlays"]["AAPL"]
        self.assertEqual(overlay["capital"]["bias"], "bullish")
        self.assertIn(overlay["verdict"], {"confirming", "caution", "conflict"})
        self.assertIn("capital", overlay)
        self.assertIn("technical", overlay)
        self.assertIn("derivatives", overlay)


if __name__ == "__main__":
    unittest.main()
