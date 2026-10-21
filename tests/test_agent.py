import unittest
from unittest.mock import patch

import agent


def sample_asset(name, contribution, change=0.5):
    return {
        "name": name,
        "change_pct_5d": change,
        "trend_label": "up over 5 trading days",
        "bias_contribution": contribution,
    }


class SnapshotTests(unittest.TestCase):
    @patch("agent.tradingview_technical_rating", return_value=0.4)
    @patch(
        "agent.fetch_instrument",
        side_effect=[
            sample_asset("US Dollar Index", 0.2),
            sample_asset("WTI crude oil", 0.1),
            sample_asset("Gold futures", 0.2),
        ],
    )
    def test_snapshot_percentages_and_tradingview_source(self, _fetch, _rating):
        snapshot = agent.build_snapshot()

        self.assertEqual(snapshot["fundamental_bias"]["bullish_pct"], 75)
        self.assertEqual(snapshot["fundamental_bias"]["bearish_pct"], 25)
        self.assertEqual(snapshot["fundamental_bias"]["direction"], "Bullish")
        self.assertEqual(snapshot["technical_bias"]["bullish_pct"], 70)
        self.assertEqual(snapshot["technical_bias"]["source"], "TradingView")
        self.assertEqual(snapshot["technical_bias"]["timeframe"], "30m")

    @patch("agent.yahoo_chart", return_value=([1, 2], [100.0, 102.0]))
    def test_rising_dollar_has_bearish_gold_contribution(self, _chart):
        result = agent.fetch_instrument("DXY", agent.INSTRUMENTS["DXY"])

        self.assertEqual(result["change_pct_5d"], 2.0)
        self.assertLess(result["bias_contribution"], 0)

    @patch("agent.local_technical_rating", return_value=-0.4)
    @patch("agent.tradingview_technical_rating", side_effect=RuntimeError("unavailable"))
    @patch(
        "agent.fetch_instrument",
        side_effect=[
            sample_asset("US Dollar Index", 0.0),
            sample_asset("WTI crude oil", 0.0),
            sample_asset("Gold futures", 0.0),
        ],
    )
    def test_local_technical_fallback_is_labeled(self, _fetch, _rating, _local):
        snapshot = agent.build_snapshot()

        self.assertEqual(snapshot["technical_bias"]["bullish_pct"], 30)
        self.assertEqual(snapshot["technical_bias"]["source"], "Local estimate")
        self.assertEqual(snapshot["technical_bias"]["timeframe"], "30m")


if __name__ == "__main__":
    unittest.main()
