#!/usr/bin/env python3
"""Build a free, rules-based XAUUSD bias snapshot for the static dashboard."""

from __future__ import annotations

import json
import math
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
LATEST_PATH = DATA_DIR / "latest.json"
HISTORY_PATH = DATA_DIR / "history.json"
USER_AGENT = "GoldBiasDashboard/1.0 (public market dashboard)"
INSTRUMENTS = {
    "DXY": {"name": "US Dollar Index", "symbol": "DX-Y.NYB", "currency": "index points", "fundamental_weight": 0.60, "inverse": True},
    "WTI": {"name": "WTI crude oil", "symbol": "CL=F", "currency": "USD / barrel", "fundamental_weight": 0.40, "inverse": False},
    "GOLD": {"name": "Gold futures", "symbol": "GC=F", "currency": "USD / troy oz", "fundamental_weight": 0.0, "inverse": False},
}


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
    score = sum(asset["bias_contribution"] for asset in assets.values())
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
            "weight_pct": round(spec["fundamental_weight"] * 100),
            "change_pct_24h": asset["change_pct_24h"],
            "change_pct_5d": asset["change_pct_5d"],
            "trend_label": asset["short_trend_label"],
            "impact_pct_points": round(contribution * 50, 1),
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
    if bullish_pct >= 55:
        summary = f"The live market-based fundamental backdrop leans bullish for gold ({bullish_pct}% model score). "
    elif bullish_pct <= 45:
        summary = f"The live market-based fundamental backdrop leans bearish for gold ({bullish_pct}% model score). "
    else:
        summary = f"The live market-based fundamental backdrop is mixed for gold ({bullish_pct}% model score). "
    summary += f"USD direction and oil/inflation context: {len(supportive)} of 2 factors support gold"
    if opposing:
        opposing_label = "factor weighs" if len(opposing) == 1 else "factors weigh"
        summary += f"; {len(opposing)} {opposing_label} against it"
    summary += f". {strongest['name']} is the largest current influence ({strongest['impact_pct_points']:+.1f} score points). Gold's 24-hour move is shown separately as price confirmation, not counted as a fundamental input."
    refreshed_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    return {
        "generated_at": refreshed_at,
        "last_successful_refresh_at": refreshed_at,
        "fundamental_bias": {
            "bullish_pct": bullish_pct,
            "bearish_pct": 100 - bullish_pct,
            "direction": direction,
            "method": "Live 30-minute market snapshot, refreshed every 30 minutes. Fundamental score uses the latest 24-hour DXY move inverted (60%) and WTI move as an inflation-pressure proxy (40%), each capped at +/-2%. Gold price is shown separately for confirmation and is not counted in the fundamental score.",
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
