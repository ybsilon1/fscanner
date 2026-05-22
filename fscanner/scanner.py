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
Always writes per-run pages to the GitHub Wiki.
"""

import datetime
import os
import tempfile
import time
from pathlib import Path
from string import Template
from typing import Any

import matplotlib
import requests

matplotlib.use("Agg")

import matplotlib.gridspec as gridspec
import matplotlib.patches as mpatches
import matplotlib.pyplot as plt

# ── Config ─────────────────────────────────────────────────────────────────────
_REPO_ROOT = Path(__file__).parent.parent
_TEMPLATES = Path(__file__).parent / "templates"

_RATE_LIMIT = 7  # max calls allowed per 60-second window (free tier is 8; keep one spare)
_call_times: list[float] = []  # monotonic timestamps of recent calls

TWELVE_DATA_KEY = None
TELEGRAM_TOKEN = None
TELEGRAM_CHAT_ID = None


def _render(template_name: str, **kwargs: object) -> str:
    return Template((_TEMPLATES / template_name).read_text()).substitute(**kwargs)


def _require_env(name: str) -> str:
    val = os.environ.get(name)
    if not val:
        raise RuntimeError(f"Missing required env var: {name}")
    return val


# ── Pairs ──────────────────────────────────────────────────────────────────────
PAIRS = [
    # BIS Top 15 (hard figures, April 2025)
    "EUR/USD",
    "USD/JPY",
    "GBP/USD",
    "USD/AUD",
    "USD/CAD",
    "USD/CHF",
    "USD/MXN",
    "USD/SGD",
    "USD/HKD",
    "USD/NOK",
    "USD/SEK",
    "USD/NZD",
    "EUR/JPY",
    "EUR/CHF",
    "EUR/CAD",
    "EUR/AUD",
    "EUR/NZD",
    "GBP/JPY",
    "GBP/CHF",
    "GBP/CAD",
    "GBP/AUD",
    "GBP/NZD",
    "AUD/JPY",
    "AUD/CHF",
    "AUD/CAD",
    "AUD/NZD",
    "NZD/JPY",
    "NZD/CHF",
    "NZD/CAD",
    "CAD/JPY",
    "CAD/CHF",
    "CHF/JPY",
    ]

# ── Twelve Data helpers ────────────────────────────────────────────────────────


def _rate_limit_wait() -> None:
    """Block until sending the next request would not exceed _RATE_LIMIT calls per 60 s."""
    while True:
        now = time.monotonic()
        # Drop timestamps outside the 60-second window
        cutoff = now - 60.0
        while _call_times and _call_times[0] <= cutoff:
            _call_times.pop(0)
        if len(_call_times) < _RATE_LIMIT:
            return
        # Window is full — sleep until the oldest call ages out
        wait = _call_times[0] - cutoff
        print(f"  rate-limit: waiting {wait:.0f}s...")
        time.sleep(wait)


def api_get(endpoint: str, params: dict[str, Any]) -> dict[str, Any]:
    params["apikey"] = TWELVE_DATA_KEY
    for attempt in range(3):
        _rate_limit_wait()
        try:
            _call_times.append(time.monotonic())
            r = requests.get(f"https://api.twelvedata.com/{endpoint}", params=params, timeout=30)
            r.raise_for_status()
            data = r.json()
            if data.get("code") == 429:
                print(f"  429 rate-limited (attempt {attempt + 1}), retrying in 65s...")
                time.sleep(65)
                continue
            return data
        except Exception as e:
            print(f"  API error (attempt {attempt + 1}): {e}")
            time.sleep(15)
    print(f"  {endpoint} failed after 3 attempts — skipping")
    return {}


def _parse_candle(c: dict) -> dict[str, Any]:
    return {
        "datetime": c["datetime"],
        "open": float(c["open"]),
        "high": float(c["high"]),
        "low": float(c["low"]),
        "close": float(c["close"]),
    }


def get_d1(symbol: str) -> tuple[dict, float] | None:
    """Fetch yesterday's completed D1 candle and today's current price for one symbol."""
    data = api_get("time_series", {"symbol": symbol, "interval": "1day", "outputsize": 2})
    if data.get("status") == "error" or "values" not in data:
        return None
    values = data["values"]
    return (_parse_candle(values[1]), float(values[0]["close"])) if len(values) >= 2 else None

#removing the m20
#def get_m20(symbol: str, count: int = 12) -> list[dict] | None:
#    """Fetch the last `count` completed M20 candles for one symbol."""
#   data = api_get("time_series", {"symbol": symbol, "interval": "20min", "outputsize": count + 1})
#    if data.get("status") == "error" or "values" not in data:
#        return None
#    raw = data["values"]
#    return [_parse_candle(c) for c in reversed(raw[1:])] if len(raw) >= 2 else None


# ── Signal detection ───────────────────────────────────────────────────────────


def in_hot_zone(price: float, d1: dict) -> tuple[bool, float]:
    """Return (is_in_zone, pct_from_bottom). Hot zone = bottom 20% of D1 range."""
    rng = d1["high"] - d1["low"]
    if rng == 0:
        return False, 0.0
    zone_top = d1["low"] + rng * 0.20
    pct = (price - d1["low"]) / rng * 100
    return price >= d1["low"] and price <= zone_top, round(pct, 1)


#def is_red(c: dict) -> bool:
#    return c["close"] < c["open"]


#def is_green(c: dict) -> bool:
#    return c["close"] >= c["open"]


#def has_lower_wick(c: dict, min_wick_pct: float = 0.1) -> bool:
#    """Lower wick must be at least min_wick_pct of the candle range."""
#    body_bottom = min(c["open"], c["close"])
#    candle_range = c["high"] - c["low"]
#    if candle_range == 0:
#        return False
#    wick = body_bottom - c["low"]
#    return wick / candle_range >= min_wick_pct


#def find_pattern(m20: list[dict[str, Any]]) -> dict[str, Any] | None:
#    """Scan for: green candle preceded by 3–5 red candles, last red has lower wick.
#
#    Checks longest streak first (5 → 3) so the strongest signal is preferred.
#   """
#    if len(m20) < 4:
#        return None
#
#    last = m20[-1]
#    if not is_green(last):
#        return None
#
#    for streak_len in range(5, 2, -1):  # prefer longest streak
#        end_idx = len(m20) - 2
#        start_idx = end_idx - streak_len + 1
#        if start_idx < 0:
#            continue
#
#        streak = m20[start_idx : end_idx + 1]
#
#        if not all(is_red(c) for c in streak):
#            continue
#
#        last_red = streak[-1]
#        if not has_lower_wick(last_red):
#            continue
#
#        return {
#            "streak_len": streak_len,
#            "streak_start": streak[0]["datetime"],
#            "last_red": last_red,
#            "green_candle": last,
#        }
#
#    return None
#

# ── Chart generation ───────────────────────────────────────────────────────────


def generate_chart(
    symbol: str, d1: dict, m20: list[dict], current_price: float, pattern: dict, pct: float
) -> Path:
    fig = plt.figure(figsize=(12, 7), facecolor="#0d1117")
    gs = gridspec.GridSpec(2, 1, height_ratios=[1, 2.2], hspace=0.35)

    # ── TOP: D1 overview ──────────────────────────────────────────────────────
    ax1 = fig.add_subplot(gs[0])
    ax1.set_facecolor("#0d1117")

    rng = d1["high"] - d1["low"]
    zone_top = d1["low"] + rng * 0.20
    mid = d1["low"] + rng * 0.50

    ax1.axhspan(d1["low"], zone_top, color="#e06060", alpha=0.12)
    ax1.axhline(d1["high"], color="#d4a017", lw=1.4, ls="-", label=f"H {d1['high']:.5f}")
    ax1.axhline(mid, color="#5b8db8", lw=1.2, ls="--", label=f"M {mid:.5f}")
    ax1.axhline(zone_top, color="#e67e22", lw=1.2, ls="--", label=f"20% {zone_top:.5f}")
    ax1.axhline(d1["low"], color="#c0392b", lw=1.4, ls="-", label=f"L {d1['low']:.5f}")
    ax1.axhline(current_price, color="#00e5ff", lw=1.0, ls=":", label=f"Now {current_price:.5f}")

    body_col = "#26a69a" if d1["close"] >= d1["open"] else "#ef5350"
    ax1.plot([0.5, 0.5], [d1["low"], d1["high"]], color="#888", lw=1.5)
    ax1.add_patch(
        mpatches.FancyBboxPatch(
            (0.5 - 0.15, min(d1["open"], d1["close"])),
            0.30,
            abs(d1["close"] - d1["open"]) or rng * 0.005,
            boxstyle="round,pad=0.001",
            facecolor=body_col,
            edgecolor=body_col,
            zorder=3,
        )
    )

    ax1.set_xlim(0, 1)
    margin = rng * 0.25
    ax1.set_ylim(d1["low"] - margin, d1["high"] + margin)
    ax1.set_xticks([])
    ax1.tick_params(colors="#888", labelsize=7)
    ax1.yaxis.tick_right()
    ax1.set_title(
        f"{symbol}  ·  D1 ({d1['datetime']})  ·  price at {pct:.1f}% from low",
        color="#cccccc",
        fontsize=9,
        pad=6,
        fontfamily="monospace",
    )
    ax1.legend(
        loc="lower left",
        fontsize=7,
        facecolor="#1a1f2e",
        edgecolor="#333",
        labelcolor="#cccccc",
        ncol=5,
    )
    for spine in ax1.spines.values():
        spine.set_edgecolor("#333")

    # ── BOTTOM: M20 candles ───────────────────────────────────────────────────
    ax2 = fig.add_subplot(gs[1])
    ax2.set_facecolor("#0d1117")

    display = m20[-10:]
    streak_start_dt = pattern["streak_start"]

    for i, c in enumerate(display):
        bull = c["close"] >= c["open"]
        color = "#26a69a" if bull else "#ef5350"
        in_streak = c["datetime"] >= streak_start_dt
        is_last_green = c == pattern["green_candle"]
        alpha = 1.0 if (in_streak or is_last_green) else 0.45

        body_b = min(c["open"], c["close"])
        body_h = abs(c["close"] - c["open"]) or (c["high"] - c["low"]) * 0.02
        ax2.plot([i, i], [c["low"], c["high"]], color=color, lw=1.5, alpha=alpha)
        ax2.add_patch(
            mpatches.FancyBboxPatch(
                (i - 0.28, body_b),
                0.56,
                body_h,
                boxstyle="round,pad=0.001",
                facecolor=color,
                edgecolor=color,
                alpha=alpha,
                zorder=3,
            )
        )

        if c == pattern["last_red"]:
            ax2.annotate(
                "wick",
                xy=(i, c["low"]),
                xytext=(i + 0.5, c["low"] - (c["high"] - c["low"]) * 0.4),
                color="#ffcc00",
                fontsize=7,
                fontfamily="monospace",
                arrowprops=dict(arrowstyle="->", color="#ffcc00", lw=0.8),
            )

        if is_last_green:
            ax2.annotate(
                "reversal",
                xy=(i, c["high"]),
                xytext=(i - 0.5, c["high"] + (c["high"] - c["low"]) * 0.5),
                color="#00e5ff",
                fontsize=7,
                fontfamily="monospace",
                arrowprops=dict(arrowstyle="->", color="#00e5ff", lw=0.8),
            )

    ax2.set_xticks(range(len(display)))
    ax2.set_xticklabels(
        [c["datetime"][11:16] for c in display], color="#666", fontsize=7, fontfamily="monospace"
    )
    ax2.tick_params(colors="#888", labelsize=7)
    ax2.yaxis.tick_right()
    ax2.set_xlim(-0.8, len(display) - 0.2)
    ax2.set_title(
        f"M20 pattern  ·  {pattern['streak_len']} red + lower wick → green reversal",
        color="#cccccc",
        fontsize=9,
        pad=6,
        fontfamily="monospace",
    )
    for spine in ax2.spines.values():
        spine.set_edgecolor("#333")

    fig.suptitle(
        f"CANDIDATE: {symbol}",
        color="#ffffff",
        fontsize=13,
        fontweight="bold",
        fontfamily="monospace",
        y=0.98,
    )

    path = Path(tempfile.gettempdir()) / f"chart_{symbol.replace('/', '')}.png"
    plt.tight_layout(rect=(0, 0, 1, 0.96))
    plt.savefig(path, dpi=130, bbox_inches="tight", facecolor=fig.get_facecolor())
    plt.close()
    return path


# ── Telegram ───────────────────────────────────────────────────────────────────


def send_telegram_alert(
    symbol: str, d1: dict, pattern: dict, current_price: float, pct: float, chart_path: Path
):
    rng = d1["high"] - d1["low"]
    zone_top = d1["low"] + rng * 0.20

    caption = (
        f"*CANDIDATE: {symbol}*\n\n"
        f"D1 levels ({d1['datetime']})\n"
        f"  H  `{d1['high']:.5f}`\n"
        f"  20% `{zone_top:.5f}`  ← zone top\n"
        f"  L  `{d1['low']:.5f}`\n\n"
        f"Price `{current_price:.5f}` is *{pct:.1f}%* above D1 low\n\n"
        f"M20 pattern: {pattern['streak_len']} red candles + lower wick → green reversal"
    )

    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendPhoto"
    with open(chart_path, "rb") as img:
        resp = requests.post(
            url,
            data={"chat_id": TELEGRAM_CHAT_ID, "caption": caption, "parse_mode": "Markdown"},
            files={"photo": img},
            timeout=20,
        )
    resp.raise_for_status()


# ── Wiki markdown (for GitHub Wiki) ───────────────────────────────────────────


def build_wiki_page(results: list[dict], run_dt: datetime.datetime) -> tuple[str, str]:
    """Return (page_filename, markdown_content) for the current scan."""
    candidates = [r for r in results if r.get("candidate")]
    errors = [r for r in results if r.get("error")]
    page_name = f"Scan-{run_dt.strftime('%Y-%m-%d-%H%M')}"
    content = _render(
        "wiki_scan.md",
        timestamp=run_dt.strftime("%Y-%m-%d %H:%M UTC"),
        candidate_count=len(candidates),
        total=len(results),
        error_count=len(errors),
        candidates_section=_candidates_section(candidates, include_chart=False),
        all_pairs_table="\n".join(_all_pairs_table(results)),
    )
    return page_name, content


def _candidates_section(candidates: list[dict], *, include_chart: bool) -> str:
    if not candidates:
        return "## Candidates\n\n_No candidates found in this scan._"
    lines = ["## Candidates", ""]
    for r in candidates:
        d1 = r["d1"]
        rng = d1["high"] - d1["low"]
        zt = d1["low"] + rng * 0.20
        lines += [
            f"### {r['symbol']}",
            "",
            "| Field | Value |",
            "| --- | --- |",
            f"| Price | `{r['price']:.5f}` |",
            f"| % from D1 low | `{r['pct_from_low']:.1f}%` |",
            f"| D1 high | `{d1['high']:.5f}` |",
            f"| Zone top (20%) | `{zt:.5f}` |",
            f"| D1 low | `{d1['low']:.5f}` |",
            f"| M20 pattern | {r['pattern']['streak_len']} red + lower wick → green |",
            "",
        ]
        if include_chart:
            cf = f"chart_{r['symbol'].replace('/', '')}.png"
            lines += [f"![{r['symbol']} chart]({cf})", ""]
    return "\n".join(lines)


def _all_pairs_table(results: list[dict]) -> list[str]:
    lines = [
        "## All pairs",
        "",
        "| Pair | Price | % from low | Hot zone | Pattern | Status |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for r in results:
        if r.get("error"):
            lines.append(f"| {r['symbol']} | — | — | — | — | `error: {r['error']}` |")
            continue
        in_zone = r.get("pct_from_low") is not None and 0 <= r["pct_from_low"] <= 20
        pattern = r.get("pattern")
        price_str = f"`{r['price']:.5f}`" if r.get("price") else "—"
        pct_str = f"{r['pct_from_low']:.1f}%" if r.get("pct_from_low") is not None else "—"
        zone_str = "in zone" if in_zone else "—"
        pat_str = f"{pattern['streak_len']}R+G" if pattern else "—"
        status = "**candidate**" if r.get("candidate") else "—"
        lines.append(
            f"| {r['symbol']} | {price_str} | {pct_str} | {zone_str} | {pat_str} | {status} |"
        )
    return lines


def update_wiki_index(
    wiki_dir: Path, page_name: str, run_dt: datetime.datetime, candidate_count: int, total: int
):
    """Prepend a row to Home.md's run table, creating the file from template if needed."""
    home = wiki_dir / "Home.md"
    new_row = (
        f"| [{run_dt.strftime('%Y-%m-%d %H:%M UTC')}]({page_name}) | {candidate_count} | {total} |"
    )
    content = home.read_text() if home.exists() else (_TEMPLATES / "wiki_home.md").read_text()

    header_marker = "| --- | --- | --- |\n"
    if header_marker in content:
        content = content.replace(header_marker, header_marker + new_row + "\n", 1)
    else:
        content += new_row + "\n"

    home.write_text(content)


def save_wiki(results: list[dict], run_dt: datetime.datetime):
    wiki_dir = _REPO_ROOT / "wiki"
    if not wiki_dir.exists():
        print(f"  Wiki dir {wiki_dir} not found — skipping wiki output")
        return

    candidates = [r for r in results if r.get("candidate")]
    page_name, content = build_wiki_page(results, run_dt)
    (wiki_dir / f"{page_name}.md").write_text(content)
    update_wiki_index(wiki_dir, page_name, run_dt, len(candidates), len(results))
    print(f"  Wiki written → {wiki_dir / page_name}.md")


# ── Main ───────────────────────────────────────────────────────────────────────


def main():
    global TWELVE_DATA_KEY, TELEGRAM_TOKEN, TELEGRAM_CHAT_ID
    TWELVE_DATA_KEY = _require_env("TWELVE_DATA_KEY")
    TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
    TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print("  Telegram not configured — alerts disabled")

    run_dt = datetime.datetime.now(datetime.UTC)
    print(f"=== Forex Scanner  {run_dt.strftime('%Y-%m-%d %H:%M UTC')} ===")

    # ── Step 1: fetch D1 for each pair individually ───────────────────────────
    print(f"\nFetching D1 data for {len(PAIRS)} pairs...")
    results: list[dict[str, Any]] = []
    in_zone_symbols: list[str] = []

    for symbol in PAIRS:
        result: dict[str, Any] = {"symbol": symbol, "candidate": False, "error": None}
        pair_data = get_d1(symbol)

        if pair_data is None:
            result["error"] = "no D1 data"
            results.append(result)
            print(f"  {symbol:<12} no data")
            continue

        d1, price = pair_data
        rng = d1["high"] - d1["low"]
        zone_top = d1["low"] + rng * 0.20
        in_zone, pct = in_hot_zone(price, d1)

        result["price"] = price
        result["pct_from_low"] = pct
        result["d1"] = d1
        results.append(result)

        zone_label = f"{d1['low']:.5f} – {zone_top:.5f}"
        if in_zone:
            print(
                f"  {symbol:<12} price={price:.5f}  hot zone {zone_label}  ✓ IN ZONE ({pct:.1f}%)"
            )
            in_zone_symbols.append(symbol)
        else:
            print(f"  {symbol:<12} price={price:.5f}  hot zone {zone_label}  — {pct:.1f}% from low")

    # ── Step 2: fetch M20 only for pairs in the hot zone ─────────────────────
    if not in_zone_symbols:
        print("\nNo pairs in hot zone — skipping M20 fetch.")
    else:
        print(f"\nFetching M20 data for {len(in_zone_symbols)} pair(s) in zone...")

        for result in results:
            symbol = result["symbol"]
            if symbol not in in_zone_symbols:
                continue

            m20 = get_m20(symbol)
            if not m20:
                result["error"] = "no M20 data"
                continue

            pattern = find_pattern(m20)
            if not pattern:
                print(f"  {symbol:<12} no M20 pattern")
                continue

            result["candidate"] = True
            result["pattern"] = {
                "streak_len": pattern["streak_len"],
                "streak_start": pattern["streak_start"],
            }
            print(f"  {symbol:<12} *** CANDIDATE! {pattern['streak_len']} red + wick → green ***")

            chart = generate_chart(
                symbol, result["d1"], m20, result["price"], pattern, result["pct_from_low"]
            )
            if TELEGRAM_TOKEN and TELEGRAM_CHAT_ID:
                send_telegram_alert(
                    symbol, result["d1"], pattern, result["price"], result["pct_from_low"], chart
                )

    # ── Step 3: save outputs ─────────────────────────────────────────────────
    print()
    save_wiki(results, run_dt)

    candidates = [r for r in results if r.get("candidate")]
    print(f"\n=== Done: {len(candidates)}/{len(PAIRS)} candidates found ===")
    for c in candidates:
        print(f"  {c['symbol']}  ({c['pct_from_low']:.1f}% from low)")


if __name__ == "__main__":
    main()
