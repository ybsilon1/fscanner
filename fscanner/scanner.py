#!/usr/bin/env python3
"""
Forex Candidate Scanner
=======================
Scans 50 forex pairs every hour via GitHub Actions.

Candidate conditions (ALL must be true):
  1. Current price is within the bottom 20% of yesterday's D1 candle range
  2. On M20: 3-5 consecutive red candles where the last red candle has a lower wick
  3. The candle immediately following those red candles is green

On a match: sends a Telegram alert with a chart image.
Always writes results to status.json for the GitHub Pages dashboard.
"""

import os
import json
import time
import datetime
import requests
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import matplotlib.gridspec as gridspec
from pathlib import Path

# ── Config from environment variables (set in GitHub Actions secrets) ──────────
TWELVE_DATA_KEY  = os.environ["TWELVE_DATA_KEY"]
TELEGRAM_TOKEN   = os.environ["TELEGRAM_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]

OUTPUT_DIR = Path("docs")          # GitHub Pages serves from /docs
OUTPUT_DIR.mkdir(exist_ok=True)

# ── All 50 pairs ───────────────────────────────────────────────────────────────
PAIRS = [
    # BIS Top 15 (hard figures, April 2025)
    "EUR/USD", "USD/JPY", "GBP/USD", "USD/CNY", "USD/AUD",
    "USD/CAD", "USD/CHF", "USD/MXN", "USD/SGD", "USD/HKD",
    "USD/NOK", "USD/SEK", "USD/NZD", "USD/INR", "USD/KRW",
    # Major crosses
    "EUR/GBP", "EUR/JPY", "EUR/CHF", "EUR/CAD", "EUR/AUD",
    "EUR/NZD", "GBP/JPY", "GBP/CHF", "GBP/CAD", "GBP/AUD",
    "GBP/NZD", "AUD/JPY", "AUD/CHF", "AUD/CAD", "AUD/NZD",
    "NZD/JPY", "NZD/CHF", "NZD/CAD", "CAD/JPY", "CAD/CHF",
    "CHF/JPY",
    # Minors & exotics
    "USD/TRY", "USD/ZAR", "USD/BRL", "USD/PLN", "USD/CZK",
    "USD/HUF", "USD/DKK", "USD/THB", "USD/MYR", "USD/PHP",
    "EUR/PLN", "EUR/TRY", "EUR/ZAR", "GBP/PLN",
]

# ── Twelve Data helpers ────────────────────────────────────────────────────────

def api_get(endpoint: str, params: dict) -> dict:
    """Rate-limited GET to Twelve Data (8 req/min on free tier)."""
    params["apikey"] = TWELVE_DATA_KEY
    for attempt in range(3):
        try:
            r = requests.get(
                f"https://api.twelvedata.com/{endpoint}",
                params=params, timeout=15
            )
            r.raise_for_status()
            data = r.json()
            # Free tier: 8 requests/min → sleep 8s between calls
            time.sleep(8)
            return data
        except Exception as e:
            print(f"  API error (attempt {attempt+1}): {e}")
            time.sleep(15)
    return {}


def get_d1_candle(symbol: str) -> dict | None:
    """Return the last COMPLETED D1 candle for symbol."""
    data = api_get("time_series", {
        "symbol": symbol, "interval": "1day", "outputsize": 2
    })
    if "values" not in data or len(data["values"]) < 2:
        return None
    c = data["values"][1]   # index 0 = today (may be incomplete)
    return {
        "date":  c["datetime"],
        "open":  float(c["open"]),
        "high":  float(c["high"]),
        "low":   float(c["low"]),
        "close": float(c["close"]),
    }


def get_m20_candles(symbol: str, count: int = 12) -> list[dict] | None:
    """Return the last `count` completed M20 candles (oldest first)."""
    data = api_get("time_series", {
        "symbol": symbol, "interval": "20min", "outputsize": count + 1
    })
    if "values" not in data:
        return None
    candles = []
    for c in reversed(data["values"][1:]):   # drop most recent (may be open)
        candles.append({
            "datetime": c["datetime"],
            "open":  float(c["open"]),
            "high":  float(c["high"]),
            "low":   float(c["low"]),
            "close": float(c["close"]),
        })
    return candles


def get_current_price(symbol: str) -> float | None:
    data = api_get("price", {"symbol": symbol})
    try:
        return float(data["price"])
    except Exception:
        return None


# ── Signal detection ───────────────────────────────────────────────────────────

def in_hot_zone(price: float, d1: dict) -> tuple[bool, float]:
    """
    Returns (is_in_zone, pct_from_bottom).
    Hot zone = bottom 20% of D1 range.
    """
    rng = d1["high"] - d1["low"]
    if rng == 0:
        return False, 0.0
    zone_top = d1["low"] + rng * 0.20
    pct = (price - d1["low"]) / rng * 100
    return price >= d1["low"] and price <= zone_top, round(pct, 1)


def is_red(c: dict) -> bool:
    return c["close"] < c["open"]


def is_green(c: dict) -> bool:
    return c["close"] >= c["open"]


def has_lower_wick(c: dict, min_wick_pct: float = 0.1) -> bool:
    """
    Lower wick exists if low is meaningfully below the body.
    min_wick_pct: wick must be at least 10% of the candle range.
    """
    body_bottom = min(c["open"], c["close"])
    candle_range = c["high"] - c["low"]
    if candle_range == 0:
        return False
    wick = body_bottom - c["low"]
    return wick / candle_range >= min_wick_pct


def find_pattern(m20: list[dict]) -> dict | None:
    """
    Scan from newest backwards for:
      - 1 green candle  (most recent)
      - preceded by 3-5 red candles, last of which has a lower wick

    Returns match details or None.
    """
    if len(m20) < 4:
        return None

    # The most recent candle must be green
    last = m20[-1]
    if not is_green(last):
        return None

    # Look back through the preceding candles for a red streak
    for streak_len in range(3, 6):   # 3, 4, or 5
        end_idx = len(m20) - 2       # candle just before the green one
        start_idx = end_idx - streak_len + 1
        if start_idx < 0:
            continue

        streak = m20[start_idx : end_idx + 1]

        # All must be red
        if not all(is_red(c) for c in streak):
            continue

        # Last red candle must have a lower wick
        last_red = streak[-1]
        if not has_lower_wick(last_red):
            continue

        return {
            "streak_len": streak_len,
            "streak_start": streak[0]["datetime"],
            "last_red": last_red,
            "green_candle": last,
        }

    return None


# ── Chart generation ───────────────────────────────────────────────────────────

def generate_chart(symbol: str, d1: dict, m20: list[dict],
                   current_price: float, pattern: dict, pct: float) -> Path:
    fig = plt.figure(figsize=(12, 7), facecolor="#0d1117")
    gs  = gridspec.GridSpec(2, 1, height_ratios=[1, 2.2], hspace=0.35)

    # ── TOP: D1 overview ──────────────────────────────────────────────────────
    ax1 = fig.add_subplot(gs[0])
    ax1.set_facecolor("#0d1117")

    rng      = d1["high"] - d1["low"]
    zone_top = d1["low"] + rng * 0.20
    mid      = d1["low"] + rng * 0.50

    # Hot zone shading
    ax1.axhspan(d1["low"], zone_top, color="#e06060", alpha=0.12)

    # Level lines
    ax1.axhline(d1["high"],  color="#d4a017", lw=1.4, ls="-",  label=f'H {d1["high"]:.5f}')
    ax1.axhline(mid,         color="#5b8db8", lw=1.2, ls="--", label=f'M {mid:.5f}')
    ax1.axhline(zone_top,    color="#e67e22", lw=1.2, ls="--", label=f'20% {zone_top:.5f}')
    ax1.axhline(d1["low"],   color="#c0392b", lw=1.4, ls="-",  label=f'L {d1["low"]:.5f}')
    ax1.axhline(current_price, color="#00e5ff", lw=1.0, ls=":", label=f'Now {current_price:.5f}')

    # D1 candle body
    body_col = "#26a69a" if d1["close"] >= d1["open"] else "#ef5350"
    ax1.plot([0.5, 0.5], [d1["low"], d1["high"]], color="#888", lw=1.5)
    ax1.add_patch(mpatches.FancyBboxPatch(
        (0.5 - 0.15, min(d1["open"], d1["close"])),
        0.30, abs(d1["close"] - d1["open"]) or rng * 0.005,
        boxstyle="round,pad=0.001", facecolor=body_col, edgecolor=body_col, zorder=3
    ))

    ax1.set_xlim(0, 1)
    margin = rng * 0.25
    ax1.set_ylim(d1["low"] - margin, d1["high"] + margin)
    ax1.set_xticks([])
    ax1.tick_params(colors="#888", labelsize=7)
    ax1.yaxis.tick_right()
    ax1.set_title(f"{symbol}  ·  D1 ({d1['date']})  ·  price at {pct:.1f}% from low",
                  color="#cccccc", fontsize=9, pad=6, fontfamily="monospace")
    leg = ax1.legend(loc="lower left", fontsize=7, facecolor="#1a1f2e",
                     edgecolor="#333", labelcolor="#cccccc", ncol=5)
    for spine in ax1.spines.values():
        spine.set_edgecolor("#333")

    # ── BOTTOM: M20 candles ───────────────────────────────────────────────────
    ax2 = fig.add_subplot(gs[1])
    ax2.set_facecolor("#0d1117")

    display = m20[-10:]   # show last 10 candles
    streak_start_dt = pattern["streak_start"]

    for i, c in enumerate(display):
        bull   = c["close"] >= c["open"]
        color  = "#26a69a" if bull else "#ef5350"
        # Highlight pattern candles
        in_streak = c["datetime"] >= streak_start_dt
        is_last_green = (c == pattern["green_candle"])
        alpha = 1.0 if (in_streak or is_last_green) else 0.45

        body_b = min(c["open"], c["close"])
        body_h = abs(c["close"] - c["open"]) or (c["high"] - c["low"]) * 0.02
        ax2.plot([i, i], [c["low"], c["high"]], color=color, lw=1.5, alpha=alpha)
        ax2.add_patch(mpatches.FancyBboxPatch(
            (i - 0.28, body_b), 0.56, body_h,
            boxstyle="round,pad=0.001",
            facecolor=color, edgecolor=color, alpha=alpha, zorder=3
        ))

        # Mark the lower wick on last red candle
        if c == pattern["last_red"]:
            ax2.annotate("wick", xy=(i, c["low"]),
                         xytext=(i + 0.5, c["low"] - (c["high"] - c["low"]) * 0.4),
                         color="#ffcc00", fontsize=7, fontfamily="monospace",
                         arrowprops=dict(arrowstyle="->", color="#ffcc00", lw=0.8))

        # Mark green reversal candle
        if is_last_green:
            ax2.annotate("reversal", xy=(i, c["high"]),
                         xytext=(i - 0.5, c["high"] + (c["high"] - c["low"]) * 0.5),
                         color="#00e5ff", fontsize=7, fontfamily="monospace",
                         arrowprops=dict(arrowstyle="->", color="#00e5ff", lw=0.8))

    # X labels: short datetime
    ax2.set_xticks(range(len(display)))
    ax2.set_xticklabels(
        [c["datetime"][11:16] for c in display],
        color="#666", fontsize=7, fontfamily="monospace"
    )
    ax2.tick_params(colors="#888", labelsize=7)
    ax2.yaxis.tick_right()
    ax2.set_xlim(-0.8, len(display) - 0.2)
    ax2.set_title(f"M20 pattern  ·  {pattern['streak_len']} red + lower wick → green reversal",
                  color="#cccccc", fontsize=9, pad=6, fontfamily="monospace")
    for spine in ax2.spines.values():
        spine.set_edgecolor("#333")

    fig.suptitle(f"CANDIDATE: {symbol}", color="#ffffff",
                 fontsize=13, fontweight="bold", fontfamily="monospace", y=0.98)

    path = OUTPUT_DIR / f"chart_{symbol.replace('/', '')}.png"
    plt.tight_layout(rect=[0, 0, 1, 0.96])
    plt.savefig(path, dpi=130, bbox_inches="tight", facecolor=fig.get_facecolor())
    plt.close()
    return path


# ── Telegram ───────────────────────────────────────────────────────────────────

def send_telegram_alert(symbol: str, d1: dict, pattern: dict,
                        current_price: float, pct: float, chart_path: Path):
    rng      = d1["high"] - d1["low"]
    zone_top = d1["low"] + rng * 0.20

    caption = (
        f"*CANDIDATE: {symbol}*\n\n"
        f"D1 levels ({d1['date']})\n"
        f"  H  `{d1['high']:.5f}`\n"
        f"  20% `{zone_top:.5f}`  ← zone top\n"
        f"  L  `{d1['low']:.5f}`\n\n"
        f"Price `{current_price:.5f}` is *{pct:.1f}%* above D1 low\n\n"
        f"M20 pattern: {pattern['streak_len']} red candles + lower wick → green reversal"
    )

    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendPhoto"
    with open(chart_path, "rb") as img:
        requests.post(url, data={
            "chat_id": TELEGRAM_CHAT_ID,
            "caption": caption,
            "parse_mode": "Markdown"
        }, files={"photo": img}, timeout=20)


# ── Status JSON (for GitHub Pages dashboard) ───────────────────────────────────

def save_status(results: list[dict]):
    status = {
        "updated": datetime.datetime.utcnow().isoformat() + "Z",
        "scanned": len(results),
        "candidates": [r for r in results if r.get("candidate")],
        "all": results,
    }
    path = OUTPUT_DIR / "status.json"
    path.write_text(json.dumps(status, indent=2))
    print(f"  Status written → {path}  ({len(status['candidates'])} candidates)")


# ── Main ───────────────────────────────────────────────────────────────────────

def scan_pair(symbol: str) -> dict:
    result = {"symbol": symbol, "candidate": False, "error": None}
    print(f"\n[{symbol}]")

    try:
        # 1. Yesterday's D1 candle
        d1 = get_d1_candle(symbol)
        if not d1:
            result["error"] = "no D1 data"
            return result
        print(f"  D1: H={d1['high']} L={d1['low']}")

        # 2. Current price
        price = get_current_price(symbol)
        if price is None:
            result["error"] = "no price"
            return result
        print(f"  Price: {price}")

        # 3. Hot zone check
        in_zone, pct = in_hot_zone(price, d1)
        result["price"] = price
        result["pct_from_low"] = pct
        result["d1"] = d1
        print(f"  In hot zone: {in_zone} ({pct:.1f}% from low)")

        if not in_zone:
            return result

        # 4. M20 pattern check
        m20 = get_m20_candles(symbol, count=12)
        if not m20:
            result["error"] = "no M20 data"
            return result

        pattern = find_pattern(m20)
        print(f"  M20 pattern: {pattern is not None}")

        if not pattern:
            return result

        # ✅ All conditions met — this is a candidate
        result["candidate"] = True
        result["pattern"] = {
            "streak_len": pattern["streak_len"],
            "streak_start": pattern["streak_start"],
        }
        print(f"  *** CANDIDATE! streak={pattern['streak_len']} ***")

        # Generate chart and send alert
        chart = generate_chart(symbol, d1, m20, price, pattern, pct)
        send_telegram_alert(symbol, d1, pattern, price, pct, chart)

    except Exception as e:
        result["error"] = str(e)
        print(f"  ERROR: {e}")

    return result


def main():
    print(f"=== Forex Scanner  {datetime.datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')} ===")
    print(f"Scanning {len(PAIRS)} pairs...\n")

    results = []
    for symbol in PAIRS:
        results.append(scan_pair(symbol))

    save_status(results)

    candidates = [r for r in results if r.get("candidate")]
    print(f"\n=== Done: {len(candidates)}/{len(PAIRS)} candidates found ===")
    for c in candidates:
        print(f"  {c['symbol']}  ({c['pct_from_low']:.1f}% from low)")


if __name__ == "__main__":
    main()
