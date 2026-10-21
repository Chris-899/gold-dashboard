import unittest
from datetime import datetime, timezone
from unittest.mock import patch

import agent


def sample_asset(name, contribution, change=0.5):
    return {
        "name": name,
        "change_pct_5d": change,
        "change_pct_24h": change,
        "short_trend_label": "up over 24 hours",
        "trend_label": "up over 5 trading days",
        "bias_contribution": contribution,
        "provider_observed_at": "2026-10-21T11:00:00+00:00",
    }


def unavailable_news():
    return {
        "provider": "Google News RSS",
        "queried_at": "2026-10-21T12:00:00+00:00",
        "status": "unavailable",
        "headline_count": 0,
        "scored_count": 0,
        "bias_score": 0,
        "bias_direction": "Mixed",
        "summary": "No recent headlines could be retrieved.",
        "limitations": "Test feed",
        "failed_search_count": len(agent.NEWS_SEARCHES),
        "headlines": [],
    }


class SnapshotTests(unittest.TestCase):
    def test_headline_rules_classify_explicit_usd_and_rate_signals(self):
        bearish_for_gold = agent.classify_headline("Dollar rallies as Fed signals rates higher for longer")
        bullish_for_gold = agent.classify_headline("Greenback falls after dovish Fed signals possible rate cut")

        self.assertEqual(bearish_for_gold["direction"], "Bearish")
        self.assertEqual(bullish_for_gold["direction"], "Bullish")
        self.assertIn("USD strength", bearish_for_gold["topics"])
        self.assertIn("Dovish rates", bullish_for_gold["topics"])
        oil_and_risk = agent.classify_headline(
            "Oil prices jump as escalating Middle East tensions fuel supply fears"
        )
        self.assertEqual(oil_and_risk["direction"], "Bullish")
        self.assertIn("Rising oil prices", oil_and_risk["topics"])
        self.assertIn("Risk escalation", oil_and_risk["topics"])

    @patch("agent.request_text", return_value="""<?xml version="1.0"?>
        <rss><channel><item>
          <title>Dollar falls after dovish Fed signals possible rate cut</title>
          <link>https://news.example.com/story</link>
          <pubDate>Wed, 21 Oct 2026 12:00:00 GMT</pubDate>
          <source url="https://news.example.com">Example News</source>
        </item>
        <item>
          <title>Dollar falls after dovish Fed signals possible rate cut</title>
          <link>https://news.example.com/duplicate</link>
        </item>
        <item>
          <title>Beyond Finance Wins Gold for Company of the Year</title>
          <link>https://news.example.com/unrelated</link>
        </item>
        </channel></rss>""")
    def test_headline_feed_keeps_real_source_and_publication_time(self, request):
        news = agent.fetch_headlines(datetime(2026, 10, 21, 12, tzinfo=timezone.utc))

        self.assertEqual(news["provider"], "Google News RSS")
        self.assertEqual(news["status"], "available")
        self.assertEqual(news["headline_count"], 1)
        self.assertEqual(news["scored_count"], 1)
        self.assertEqual(news["headlines"][0]["source"], "Example News")
        self.assertEqual(news["headlines"][0]["published_at"], "2026-10-21T12:00:00+00:00")
        self.assertEqual(news["headlines"][0]["score_status"], "included in score")
        self.assertEqual(request.call_count, len(agent.NEWS_SEARCHES))

    @patch("agent.request_text", return_value="""<rss><channel><item>
        <title>Dollar rallies as Fed signals rates higher for longer</title>
        <link>https://news.example.com/old</link>
        <pubDate>Tue, 20 Oct 2026 11:59:00 GMT</pubDate>
        <source>Example News</source>
      </item></channel></rss>""")
    def test_stale_headlines_are_shown_but_excluded_from_score(self, _request):
        news = agent.fetch_headlines(datetime(2026, 10, 21, 12, tzinfo=timezone.utc))

        self.assertEqual(news["headline_count"], 1)
        self.assertEqual(news["matched_count"], 1)
        self.assertEqual(news["scored_count"], 0)
        self.assertEqual(news["headlines"][0]["score_status"], "not scored: older than 24 hours")
        self.assertIn("none affect the score", news["summary"])

    @patch("agent.request_text", return_value="""<rss><channel><item>
        <title>Gold prices steady as markets await data</title>
        <link>https://news.example.com/steady</link>
        <pubDate>Wed, 21 Oct 2026 12:00:00 GMT</pubDate>
        <source>Example News</source>
      </item></channel></rss>""")
    def test_neutral_headlines_remain_unscored(self, _request):
        news = agent.fetch_headlines()

        self.assertEqual(news["status"], "available")
        self.assertEqual(news["headline_count"], 1)
        self.assertEqual(news["scored_count"], 0)
        self.assertEqual(news["bias_score"], 0.0)
        self.assertIn("none matched", news["summary"])

    @patch("agent.request_text", side_effect=RuntimeError("feed unavailable"))
    def test_failed_headline_searches_are_reported(self, request):
        news = agent.fetch_headlines()

        self.assertEqual(news["status"], "unavailable")
        self.assertEqual(news["failed_search_count"], len(agent.NEWS_SEARCHES))
        self.assertEqual(news["headline_count"], 0)
        self.assertEqual(request.call_count, len(agent.NEWS_SEARCHES))

    @patch("agent.fetch_headlines", return_value=unavailable_news())
    @patch("agent.tradingview_technical_rating", return_value=0.4)
    @patch(
        "agent.fetch_instrument",
        side_effect=[
            sample_asset("US Dollar Index", 0.2),
            sample_asset("WTI crude oil", 0.1),
            sample_asset("Gold futures", 0.0),
        ],
    )
    def test_snapshot_percentages_and_tradingview_source(self, _fetch, _rating, _news):
        snapshot = agent.build_snapshot()

        self.assertEqual(snapshot["fundamental_bias"]["bullish_pct"], 70)
        self.assertEqual(snapshot["fundamental_bias"]["bearish_pct"], 30)
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
            [53, 47, 0],
        )
        drivers = snapshot["fundamental_bias"]["drivers"]
        self.assertEqual([driver["impact_pct_points"] for driver in drivers], [13.3, 6.7, 0.0])
        self.assertEqual(drivers[2]["direction"], "Gold price confirmation")
        self.assertIn("not counted as a fundamental input", snapshot["fundamental_bias"]["summary"])

    @patch("agent.fetch_headlines", return_value={
        "provider": "Google News RSS",
        "queried_at": "2026-10-21T12:00:00+00:00",
        "status": "available",
        "headline_count": 2,
        "scored_count": 2,
        "bias_score": 0.8,
        "bias_direction": "Bullish",
        "summary": "Two headlines scored bullish.",
        "limitations": "Test feed",
        "failed_search_count": 0,
        "headlines": [],
    })
    @patch(
        "agent.fetch_instrument",
        side_effect=[
            sample_asset("US Dollar Index", 0.2),
            sample_asset("WTI crude oil", 0.1),
            sample_asset("Gold futures", 0.0),
        ],
    )
    @patch("agent.tradingview_technical_rating", return_value=0.0)
    def test_headline_tilt_is_included_separately_in_fundamental_score(self, _rating, _fetch, _news):
        snapshot = agent.build_snapshot()

        self.assertEqual(snapshot["fundamental_bias"]["bullish_pct"], 75)
        self.assertEqual(snapshot["fundamental_bias"]["drivers"][-1]["key"], "NEWS")
        self.assertEqual(snapshot["fundamental_bias"]["drivers"][-1]["impact_pct_points"], 10.0)
        self.assertEqual(snapshot["news"]["scored_count"], 2)

    @patch("agent.fetch_headlines", return_value=unavailable_news())
    @patch("agent.tradingview_technical_rating", return_value=0.0)
    @patch(
        "agent.fetch_instrument",
        side_effect=[
            sample_asset("US Dollar Index", 0.1),
            sample_asset("WTI crude oil", -0.4, change=-0.5),
            sample_asset("Gold futures", 0.0),
        ],
    )
    def test_summary_calls_out_conflicting_fundamental_factors(self, _fetch, _rating, _news):
        summary = agent.build_snapshot()["fundamental_bias"]["summary"]

        self.assertIn("; 1 factor weighs against it", summary)

    @patch("agent.yahoo_chart", return_value=([1, 2], [100.0, 102.0]))
    def test_rising_dollar_has_bearish_gold_contribution(self, _chart):
        result = agent.fetch_instrument("DXY", agent.INSTRUMENTS["DXY"])

        self.assertEqual(result["change_pct_5d"], 2.0)
        self.assertEqual(result["change_pct_24h"], 2.0)
        self.assertLess(result["bias_contribution"], 0)

    @patch("agent.yahoo_chart", return_value=([0, 3600, 86400], [100.0, 100.5, 102.0]))
    def test_fundamental_inputs_use_30m_data_and_24h_change(self, chart):
        result = agent.fetch_instrument("WTI", agent.INSTRUMENTS["WTI"])

        chart.assert_called_once_with("CL=F", "30m", "5d")
        self.assertEqual(result["change_pct_24h"], 2.0)
        self.assertEqual(result["bias_contribution"], 0.35)

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
        agent.yahoo_chart("CL=F", "30m", "5d")

        self.assertIn("/CL=F?", request.call_args.args[0])
        self.assertIn("interval=30m", request.call_args.args[0])

    @patch("agent.yahoo_chart", return_value=(list(range(30)), [float(value) for value in range(30, 60)]))
    def test_local_technical_estimate_uses_gold_futures_30m(self, chart):
        agent.local_technical_rating()

        chart.assert_called_once_with("GC=F", "30m", "5d")

    @patch("agent.yahoo_chart", return_value=(list(range(1700000000, 1700000030)), [float(value) for value in range(30, 60)]))
    def test_local_technical_observation_uses_latest_candle_time(self, _chart):
        _, observed_at = agent.local_technical_rating_with_observation()

        self.assertEqual(observed_at, "2023-11-14T22:13:49+00:00")

    @patch("agent.local_technical_rating_with_observation", return_value=(-0.4, "2026-10-21T11:00:00+00:00"))
    @patch("agent.fetch_headlines", return_value=unavailable_news())
    @patch("agent.tradingview_technical_rating", side_effect=RuntimeError("unavailable"))
    @patch(
        "agent.fetch_instrument",
        side_effect=[
            sample_asset("US Dollar Index", 0.0),
            sample_asset("WTI crude oil", 0.0),
            sample_asset("Gold futures", 0.0),
        ],
    )
    def test_local_technical_fallback_is_labeled(self, _fetch, _rating, _news, _local):
        snapshot = agent.build_snapshot()

        self.assertEqual(snapshot["technical_bias"]["bullish_pct"], 30)
        self.assertEqual(snapshot["technical_bias"]["source"], "Local estimate")
        self.assertEqual(snapshot["technical_bias"]["timeframe"], "30m")
        self.assertEqual(snapshot["technical_bias"]["provider_observed_at"], "2026-10-21T11:00:00+00:00")
        self.assertEqual(snapshot["technical_bias"]["observation_status"], "available")


if __name__ == "__main__":
    unittest.main()
