import unittest
from unittest.mock import patch

import agent


def sample_asset(name, contribution, change=0.5):
    return {
        "name": name,
        "change_pct_5d": change,
        "trend_label": "up over 5 trading days",
        "bias_contribution": contribution,
        "provider_observed_at": "2026-10-21T11:00:00+00:00",
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
        self.assertIsNone(snapshot["technical_bias"]["provider_observed_at"])
        self.assertIn("last_successful_refresh_at", snapshot)
        self.assertTrue(all(asset.get("provider_observed_at") for asset in snapshot["assets"].values()))
        self.assertIn("fundamental backdrop leans bullish", snapshot["fundamental_bias"]["summary"])
        self.assertEqual(len(snapshot["fundamental_bias"]["drivers"]), 3)
        self.assertEqual(
            [driver["weight_pct"] for driver in snapshot["fundamental_bias"]["drivers"]],
            [45, 20, 35],
        )
        self.assertEqual(
            [driver["impact_pct_points"] for driver in snapshot["fundamental_bias"]["drivers"]],
            [10.0, 5.0, 10.0],
        )

    @patch("agent.yahoo_chart", return_value=([1, 2], [100.0, 102.0]))
    def test_rising_dollar_has_bearish_gold_contribution(self, _chart):
        result = agent.fetch_instrument("DXY", agent.INSTRUMENTS["DXY"])

        self.assertEqual(result["change_pct_5d"], 2.0)
        self.assertLess(result["bias_contribution"], 0)

    @patch(
        "agent.request_json",
        return_value={
            "chart": {
                "result": [
                    {
                        "timestamp": [1],
                        "indicators": {"quote": [{"close": [100.0]}]},
                    }
                ]
            }
        },
    )
    def test_yahoo_symbol_path_keeps_equals_unescaped(self, request):
        agent.yahoo_chart("CL=F", "1d", "1mo")

        self.assertIn("/CL=F?", request.call_args.args[0])

    @patch("agent.yahoo_chart", return_value=(list(range(30)), [float(value) for value in range(30, 60)]))
    def test_local_technical_estimate_uses_gold_futures_30m(self, chart):
        agent.local_technical_rating()

        chart.assert_called_once_with("GC=F", "30m", "5d")

    @patch("agent.yahoo_chart", return_value=(list(range(1700000000, 1700000030)), [float(value) for value in range(30, 60)]))
    def test_local_technical_observation_uses_latest_candle_time(self, _chart):
        _, observed_at = agent.local_technical_rating_with_observation()

        self.assertEqual(observed_at, "2023-11-14T22:13:49+00:00")

    @patch("agent.local_technical_rating_with_observation", return_value=(-0.4, "2026-10-21T11:00:00+00:00"))
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
        self.assertEqual(snapshot["technical_bias"]["provider_observed_at"], "2026-10-21T11:00:00+00:00")
        self.assertEqual(snapshot["technical_bias"]["observation_status"], "available")


if __name__ == "__main__":
    unittest.main()
