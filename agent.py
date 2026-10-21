#!/usr/bin/env python3
"""Build a free, rules-based XAUUSD bias snapshot for the static dashboard."""

from __future__ import annotations

import json
import math
import email.utils
import sys
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
LATEST_PATH = DATA_DIR / "latest.json"
HISTORY_PATH = DATA_DIR / "history.json"
USER_AGENT = "GoldBiasDashboard/1.0 (public market dashboard)"
INSTRUMENTS = {
    "DXY": {"name": "US Dollar Index", "symbol": "DX-Y.NYB", "currency": "index points", "fundamental_weight": 0.40, "inverse": True},
    "WTI": {"name": "WTI crude oil", "symbol": "CL=F", "currency": "USD / barrel", "fundamental_weight": 0.35, "inverse": False},
    "GOLD": {"name": "Gold futures", "symbol": "GC=F", "currency": "USD / troy oz", "fundamental_weight": 0.0, "inverse": False},
}
NEWS_SEARCHES = (
    "gold OR XAUUSD when:1d",
    "Federal Reserve OR FOMC OR inflation OR Treasury yields when:1d",
    "US dollar OR dollar index OR DXY when:1d",
    "WTI crude oil OR OPEC when:1d",
)
NEWS_SOURCE = "Google News RSS"


def request_json(url: str, payload: dict[str, Any] | None = None) -> Any:
    body = json.dumps(payload).encode() if payload is not None else None
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    if body is not None:
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=body, headers=headers, method="POST" if body is not None else "GET")
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            return json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Could not retrieve market data from {urllib.parse.urlparse(url).hostname}: {exc}") from exc


def request_text(url: str) -> str:
    request = urllib.request.Request(url, headers={
        "User-Agent": USER_AGENT,
        "Accept": "application/rss+xml, application/xml, text/xml",
    })
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            payload = response.read(2_000_001)
            if len(payload) > 2_000_000:
                raise RuntimeError("Headline RSS response exceeded the 2 MB limit")
            return payload.decode("utf-8")
    except (urllib.error.URLError, TimeoutError, UnicodeDecodeError) as exc:
        raise RuntimeError(f"Could not retrieve headlines from {urllib.parse.urlparse(url).hostname}: {exc}") from exc


def classify_headline(title: str) -> dict[str, Any]:
    text = title.casefold()
    score = 0.0
    topics = []
    signals = (
        ("USD strength", -0.75, ("dollar gains", "dollar rises", "dollar jumps", "dollar strengthens", "dollar rallies", "dxy rises", "greenback gains", "greenback rises", "dollar strengthens")),
        ("USD weakness", 0.75, ("dollar falls", "dollar slips", "dollar weakens", "dollar drops", "dxy falls", "greenback falls", "greenback weakens")),
        ("Hawkish rates", -0.75, ("hawkish fed", "fed signals hikes", "rate hike", "rates higher for longer", "higher for longer", "yields rise", "yields surge", "treasury yields climb")),
        ("Dovish rates", 0.75, ("dovish fed", "rate cut", "rates cut", "rate cuts", "yields fall", "yields slide", "treasury yields drop")),
        ("Risk escalation", 0.45, ("war escalates", "war intensifies", "conflict escalates", "attack on", "military strikes", "invasion", "safe-haven demand", "safe haven demand")),
        ("Risk de-escalation", -0.35, ("ceasefire", "peace deal", "peace talks", "tensions ease", "conflict de-escalates")),
        ("Oil supply tightens", 0.3, ("opec cuts", "opec+ cuts", "supply cuts", "supply disruption", "oil supply tightens", "crude supply falls")),
        ("Oil supply expands", -0.3, ("opec raises output", "opec+ raises output", "output hike", "supply glut", "oil inventories rise", "crude inventories build")),
        ("Weaker US data", 0.35, ("us jobs miss", "jobs data misses", "unemployment rises", "weak us jobs", "economic data disappoints", "growth slows")),
        ("Stronger US data", -0.35, ("us jobs beat", "jobs data beats", "strong us jobs", "economic data beats", "growth accelerates")),
        ("Cooling inflation", 0.35, ("inflation cools", "inflation eases", "cpi cools", "pce cools", "inflation slows")),
        ("Hotter inflation", -0.35, ("inflation heats up", "inflation accelerates", "cpi heats up", "hotter than expected inflation", "sticky inflation")),
    )
    for topic, value, phrases in signals:
        if any(phrase in text for phrase in phrases):
            topics.append(topic)
            score += value
    score = max(-1.0, min(1.0, score))
    direction = "Bullish" if score > 0.1 else "Bearish" if score < -0.1 else "Mixed / unscored"
    return {"score": score, "topics": topics, "direction": direction}


def fetch_headlines(now: datetime | None = None) -> dict[str, Any]:
    observed_time = now or datetime.now(timezone.utc)
    if observed_time.tzinfo is None:
        observed_time = observed_time.replace(tzinfo=timezone.utc)
    observed_time = observed_time.astimezone(timezone.utc)
    observed_at = observed_time.replace(microsecond=0).isoformat()
    headlines = []
    failed_searches = []
    seen = set()
    for query in NEWS_SEARCHES:
        url = "https://news.google.com/rss/search?" + urllib.parse.urlencode({
            "q": query,
            "hl": "en-US",
            "gl": "US",
            "ceid": "US:en",
        })
        try:
            root = ET.fromstring(request_text(url))
        except (RuntimeError, ET.ParseError) as exc:
            print(f"Headline search failed ({query}): {exc}", file=sys.stderr)
            failed_searches.append(query)
            continue
        for item in root.findall("./channel/item"):
            title = (item.findtext("title") or "").strip()
            link = (item.findtext("link") or "").strip()
            if not title or not link or link in seen:
                continue
            seen.add(link)
            source = item.findtext("source") or "Google News result"
            pub_date = item.findtext("pubDate")
            try:
                published_time = email.utils.parsedate_to_datetime(pub_date) if pub_date else None
                if published_time and published_time.tzinfo is None:
                    published_time = published_time.replace(tzinfo=timezone.utc)
                published_time = published_time.astimezone(timezone.utc) if published_time else None
                published_at = published_time.replace(microsecond=0).isoformat() if published_time else None
            except (TypeError, ValueError, OverflowError):
                published_time = None
                published_at = None
            classification = classify_headline(title)
            in_scoring_window = bool(
                published_time
                and observed_time - timedelta(hours=24) <= published_time <= observed_time
            )
            if not published_time:
                score_status = "not scored: publication time unavailable"
            elif published_time < observed_time - timedelta(hours=24):
                score_status = "not scored: older than 24 hours"
            elif published_time > observed_time:
                score_status = "not scored: future publication time"
            elif classification["topics"]:
                score_status = "included in score"
            else:
                score_status = "unscored by keyword rules"
            headlines.append({
                "title": title,
                "source": source,
                "url": link,
                "published_at": published_at,
                "score": classification["score"],
                "topics": classification["topics"],
                "direction": classification["direction"],
                "score_status": score_status,
                "in_scoring_window": in_scoring_window,
            })
    headlines.sort(key=lambda article: article["published_at"] or "", reverse=True)
    headlines = headlines[:20]
    matched = [article for article in headlines if article["topics"]]
    scored = [article for article in headlines if article["topics"] and article["in_scoring_window"]]
    score = sum(article["score"] for article in scored) / len(scored) if scored else 0.0
    score = max(-1.0, min(1.0, score))
    direction = "Bullish" if score > 0.1 else "Bearish" if score < -0.1 else "Mixed"
    if not headlines and len(failed_searches) == len(NEWS_SEARCHES):
        status = "unavailable"
        summary = "The free news feed was unavailable for every search. The fundamental score will use market data only."
    elif not headlines and failed_searches:
        status = "partial"
        summary = f"Some free news searches failed; the remaining searches returned no recent headlines. The fundamental score will use market data only."
    elif not headlines:
        status = "empty"
        summary = "The free news searches returned no recent headlines. The fundamental score will use market data only."
    elif failed_searches:
        status = "partial"
        summary = f"Retrieved {len(headlines)} recent headlines, but {len(failed_searches)} of {len(NEWS_SEARCHES)} news searches failed. The news tilt is {direction.lower()} from {len(scored)} headlines matching the published keyword rules."
    elif not scored and matched:
        status = "available"
        summary = f"Retrieved {len(headlines)} headlines; {len(matched)} matched keyword rules, but none had a publication time within the last 24 hours, so none affect the score."
    elif not scored:
        status = "available"
        summary = f"Retrieved {len(headlines)} recent headlines, but none matched the published directional keyword rules. Headline tone is unscored rather than assumed neutral."
    else:
        summary = f"Reviewed {len(headlines)} recent headlines; {len(scored)} matched the published directional keyword rules. Their overall news tilt is {direction.lower()}."
    return {
        "provider": NEWS_SOURCE,
        "queried_at": observed_at,
        "status": status,
        "headline_count": len(headlines),
        "matched_count": len(matched),
        "scored_count": len(scored),
        "bias_score": round(score, 3),
        "bias_direction": direction,
        "summary": summary,
        "limitations": "Free RSS headline search; only headlines with a publication time in the last 24 hours and a matching keyword rule affect the score. Tags are a heuristic, not full-article or human interpretation; headlines can be duplicated, delayed, incomplete, or misleading.",
        "failed_search_count": len(failed_searches),
        "headlines": headlines,
    }


def yahoo_chart(symbol: str, interval: str, period: str) -> tuple[list[int], list[float]]:
    encoded = urllib.parse.quote(symbol, safe="=")
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{encoded}?range={period}&interval={interval}"
    try:
        result = request_json(url)["chart"]
    except RuntimeError as exc:
        raise RuntimeError(f"Yahoo Finance chart failed for {symbol}: {exc}") from exc
    if result.get("error"):
        raise RuntimeError(f"Yahoo Finance returned an error for {symbol}: {result['error']}")
    rows = result.get("result") or []
    if not rows:
        raise RuntimeError(f"No Yahoo Finance price history returned for {symbol}")
    row = rows[0]
    quote = row.get("indicators", {}).get("quote", [{}])[0]
    timestamps = row.get("timestamp") or []
    closes = quote.get("close") or []
    valid = [(int(stamp), float(close)) for stamp, close in zip(timestamps, closes) if stamp is not None and close is not None and math.isfinite(float(close))]
    if not valid:
        raise RuntimeError(f"No valid close prices returned for {symbol}")
    return [item[0] for item in valid], [item[1] for item in valid]


def fetch_instrument(key: str, spec: dict[str, Any]) -> dict[str, Any]:
    timestamps, prices = yahoo_chart(spec["symbol"], "30m", "5d")
    current = prices[-1]
    baseline_5d = prices[0]
    cutoff_24h = timestamps[-1] - 24 * 60 * 60
    baseline_24h_index = max(
        (index for index, timestamp in enumerate(timestamps) if timestamp <= cutoff_24h),
        default=0,
    )
    baseline_24h = prices[baseline_24h_index]
    change_pct_5d = ((current / baseline_5d) - 1) * 100 if baseline_5d else 0
    change_pct_24h = ((current / baseline_24h) - 1) * 100 if baseline_24h else 0
    contribution_score = max(-1.0, min(1.0, change_pct_24h / 2.0))
    if spec["inverse"]:
        contribution_score *= -1
    contribution = spec["fundamental_weight"] * contribution_score
    if change_pct_5d > 0.05:
        trend = "up over 5 trading days"
    elif change_pct_5d < -0.05:
        trend = "down over 5 trading days"
    else:
        trend = "roughly flat over 5 trading days"
    if change_pct_24h > 0.05:
        short_trend = "up over 24 hours"
    elif change_pct_24h < -0.05:
        short_trend = "down over 24 hours"
    else:
        short_trend = "roughly flat over 24 hours"
    return {
        "name": spec["name"],
        "symbol": spec["symbol"],
        "provider": "Yahoo Finance",
        "currency": spec["currency"],
        "price": round(current, 5),
        "change_pct_24h": round(change_pct_24h, 3),
        "change_pct_5d": round(change_pct_5d, 3),
        "short_trend_label": short_trend,
        "trend_label": trend,
        "bias_contribution": round(contribution, 4),
        "last_price_at": datetime.fromtimestamp(timestamps[-1], timezone.utc).isoformat(),
        "provider_observed_at": datetime.fromtimestamp(timestamps[-1], timezone.utc).isoformat(),
    }


def ema(values: list[float], span: int) -> list[float]:
    alpha = 2 / (span + 1)
    output = [values[0]]
    for value in values[1:]:
        output.append(alpha * value + (1 - alpha) * output[-1])
    return output


def rsi(values: list[float], period: int = 14) -> float:
    changes = [values[index] - values[index - 1] for index in range(1, len(values))]
    if len(changes) < period:
        return 50.0
    gains = [max(change, 0) for change in changes[-period:]]
    losses = [max(-change, 0) for change in changes[-period:]]
    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period
    if avg_loss == 0:
        return 100.0 if avg_gain else 50.0
    return 100 - 100 / (1 + avg_gain / avg_loss)


def calculate_local_technical_rating(closes: list[float]) -> float:
    if len(closes) < 27:
        raise RuntimeError("Too few 30-minute candles for a local technical estimate")
    fast, slow = ema(closes, 9), ema(closes, 21)
    macd_line = [fast_value - slow_value for fast_value, slow_value in zip(fast, slow)]
    macd_signal = ema(macd_line, 9)
    last = closes[-1]
    votes = [
        1.0 if fast[-1] > slow[-1] else -1.0 if fast[-1] < slow[-1] else 0.0,
        1.0 if macd_line[-1] > macd_signal[-1] else -1.0 if macd_line[-1] < macd_signal[-1] else 0.0,
        1.0 if rsi(closes) > 55 else -1.0 if rsi(closes) < 45 else 0.0,
        1.0 if last > sum(closes[-20:]) / 20 else -1.0 if last < sum(closes[-20:]) / 20 else 0.0,
    ]
    return sum(votes) / len(votes)


def local_technical_rating_with_observation() -> tuple[float, str]:
    timestamps, closes = yahoo_chart("GC=F", "30m", "5d")
    rating = calculate_local_technical_rating(closes)
    observed_at = datetime.fromtimestamp(timestamps[-1], timezone.utc).isoformat()
    return rating, observed_at


def local_technical_rating() -> float:
    return local_technical_rating_with_observation()[0]


def tradingview_technical_rating() -> float:
    url = "https://scanner.tradingview.com/forex/scan"
    result = request_json(url, {
        "symbols": {"tickers": ["OANDA:XAUUSD"], "query": {"types": []}},
        "columns": ["Recommend.All|30"],
    })
    rows = result.get("data") or []
    if not rows or not rows[0].get("d"):
        raise RuntimeError("TradingView did not return a 30-minute technical rating for OANDA:XAUUSD")
    rating = float(rows[0]["d"][0])
    if not math.isfinite(rating) or not -1 <= rating <= 1:
        raise RuntimeError(f"TradingView returned an invalid technical rating: {rating}")
    return rating


def build_snapshot() -> dict[str, Any]:
    assets = {key: fetch_instrument(key, spec) for key, spec in INSTRUMENTS.items()}
    news = fetch_headlines()
    news_weight = 0.25 if news["scored_count"] else 0.0
    active_weight = sum(spec["fundamental_weight"] for spec in INSTRUMENTS.values()) + news_weight
    score = sum(asset["bias_contribution"] for asset in assets.values())
    if news_weight:
        score += news_weight * news["bias_score"]
    score = score / active_weight if active_weight else 0.0
    bullish_pct = round(max(0, min(100, 50 + score * 50)))
    if bullish_pct >= 55:
        direction = "Bullish"
    elif bullish_pct <= 45:
        direction = "Bearish"
    else:
        direction = "Neutral"
    try:
        rating = tradingview_technical_rating()
        technical_source = "TradingView"
        technical_observed_at = None
    except (RuntimeError, KeyError, TypeError, ValueError, IndexError, AttributeError) as exc:
        print(f"TradingView 30m rating unavailable; using local estimate: {exc}", file=sys.stderr)
        rating, technical_observed_at = local_technical_rating_with_observation()
        technical_source = "Local estimate"
    technical_pct = round(max(0, min(100, 50 + rating * 50)))
    fundamental_drivers = []
    for key, spec in INSTRUMENTS.items():
        asset = assets[key]
        contribution = asset["bias_contribution"]
        explanations = {
            "DXY": (
                "a weaker dollar can support gold"
                if contribution > 0
                else "a stronger dollar can pressure gold"
                if contribution < 0
                else "the latest dollar move is neutral in this model"
            ),
            "WTI": (
                "higher oil can add inflation-hedge support, though energy prices are only a proxy"
                if contribution > 0
                else "lower oil can reduce some inflation-hedge support, though energy prices are only a proxy"
                if contribution < 0
                else "the latest oil move is neutral in this model"
            ),
            "GOLD": (
                "gold price confirms an upward market move"
                if asset["change_pct_24h"] > 0.05
                else "gold price confirms a downward market move"
                if asset["change_pct_24h"] < -0.05
                else "gold price is roughly flat over 24 hours"
            ),
        }
        fundamental_drivers.append({
            "key": key,
            "name": asset["name"],
            "weight_pct": round(spec["fundamental_weight"] / active_weight * 100) if spec["fundamental_weight"] else 0,
            "change_pct_24h": asset["change_pct_24h"],
            "change_pct_5d": asset["change_pct_5d"],
            "trend_label": asset["short_trend_label"],
            "impact_pct_points": round(contribution / active_weight * 50, 1),
            "direction": (
                "Gold price confirmation"
                if key == "GOLD"
                else "Supports gold"
                if contribution > 0
                else "Weighs on gold"
                if contribution < 0
                else "Neutral"
            ),
            "explanation": explanations[key],
        })
    scored_drivers = [driver for driver in fundamental_drivers if driver["weight_pct"] > 0]
    supportive = [driver for driver in scored_drivers if driver["impact_pct_points"] > 0]
    opposing = [driver for driver in scored_drivers if driver["impact_pct_points"] < 0]
    strongest = max(scored_drivers, key=lambda driver: abs(driver["impact_pct_points"]))
    if news_weight:
        news_impact = round(news_weight * news["bias_score"] / active_weight * 50, 1)
        fundamental_drivers.append({
            "key": "NEWS",
            "name": "Recent news headlines",
            "weight_pct": round(news_weight * 100),
            "change_pct_24h": None,
            "change_pct_5d": None,
            "trend_label": f"{news['bias_direction'].lower()} tone across {news['scored_count']} scored headlines",
            "impact_pct_points": news_impact,
            "direction": news["bias_direction"],
            "explanation": "Keyword-tagged recent headlines; read the linked source stories for context",
        })
        strongest = max([*scored_drivers, fundamental_drivers[-1]], key=lambda driver: abs(driver["impact_pct_points"]))
    if bullish_pct >= 55:
        summary = f"The rules-based fundamental backdrop leans bullish for gold ({bullish_pct}% model score). "
    elif bullish_pct <= 45:
        summary = f"The rules-based fundamental backdrop leans bearish for gold ({bullish_pct}% model score). "
    else:
        summary = f"The rules-based fundamental backdrop is mixed for gold ({bullish_pct}% model score). "
    summary += f"USD direction and oil/inflation context: {len(supportive)} of 2 factors support gold"
    if opposing:
        opposing_label = "factor weighs" if len(opposing) == 1 else "factors weigh"
        summary += f"; {len(opposing)} {opposing_label} against it"
    if news_weight:
        summary += f". Recent headline tone is {news['bias_direction'].lower()} across {news['scored_count']} keyword-scored reports."
    elif news["status"] == "unavailable":
        summary += ". News feed unavailable, so no headline tilt is included."
    elif news["status"] == "empty":
        summary += ". The free news search returned no recent headlines, so no headline tilt is included."
    elif news["scored_count"] == 0:
        summary += ". Headlines were retrieved, but none matched the directional rules; no headline tilt is included."
    summary += f" {strongest['name']} is the largest current influence ({strongest['impact_pct_points']:+.1f} score points). Gold's 24-hour move is shown separately as price confirmation, not counted as a fundamental input."
    refreshed_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    return {
        "generated_at": refreshed_at,
        "last_successful_refresh_at": refreshed_at,
        "fundamental_bias": {
            "bullish_pct": bullish_pct,
            "bearish_pct": 100 - bullish_pct,
            "direction": direction,
            "method": "Rules-based 30-minute snapshot, refreshed every 30 minutes. Fundamental score uses 24-hour DXY move inverted (40%), WTI move as an inflation-pressure proxy (35%), and up to 25% from keyword-scored news headlines; market returns are capped at +/-2%. If no headline is directionally scored, its weight is omitted and market weights are rescaled. Gold price is separate confirmation. Scores are model outputs, not probabilities or predictions.",
            "summary": summary,
            "drivers": fundamental_drivers,
        },
        "technical_bias": {
            "bullish_pct": technical_pct,
            "bearish_pct": 100 - technical_pct,
            "source": technical_source,
            "timeframe": "30m",
            "provider_observed_at": technical_observed_at,
            "observation_status": "available" if technical_observed_at else "not provided by provider",
        },
        "news": news,
        "assets": assets,
        "disclaimer": "Informational only, not investment advice. Data can be delayed or unavailable.",
    }


def write_json(path: Path, data: Any) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    temporary.replace(path)


def main() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    snapshot = build_snapshot()
    history = []
    if HISTORY_PATH.exists():
        try:
            loaded = json.loads(HISTORY_PATH.read_text(encoding="utf-8"))
            if isinstance(loaded, list):
                history = loaded
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"Existing history file is invalid JSON: {exc}") from exc
    history.append({
        "timestamp": snapshot["generated_at"],
        "fundamental_bullish_pct": snapshot["fundamental_bias"]["bullish_pct"],
        "technical_bullish_pct": snapshot["technical_bias"]["bullish_pct"],
        "technical_source": snapshot["technical_bias"]["source"],
    })
    cutoff = datetime.now(timezone.utc) - timedelta(days=30)
    history = [point for point in history if datetime.fromisoformat(point["timestamp"].replace("Z", "+00:00")) >= cutoff]
    write_json(LATEST_PATH, snapshot)
    write_json(HISTORY_PATH, history)
    print(f"Snapshot written at {snapshot['generated_at']}: fundamental {snapshot['fundamental_bias']['bullish_pct']}% bullish; technical {snapshot['technical_bias']['bullish_pct']}% bullish ({snapshot['technical_bias']['source']}).")


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, OSError, KeyError, TypeError, ValueError) as error:
        print(f"Snapshot update failed: {error}", file=sys.stderr)
        raise SystemExit(1)
