# Forex Scanner

Scans 50 forex pairs twice a day (08:00 and 20:00 UTC) via GitHub Actions and sends a Telegram alert when a setup matches. Per-run results are published to the [Wiki](../../wiki). Zero hardware needed — runs free on GitHub.

## How it works

For each pair the scanner:

1. Fetches yesterday's completed **D1 candle** from Twelve Data (one call per pair, 8s apart)
2. Checks whether the current price is in the **bottom 20% of the D1 range** (the "hot zone")
3. If yes, fetches the last 12 **M20 candles** and looks for the pattern:
   - 3–5 consecutive **red candles**, the last of which has a **lower wick** (≥ 10% of its range)
   - followed immediately by a **green candle**
4. On a match: sends a **Telegram alert** with a chart image and records the pair as a candidate
5. Pushes a dated page to the Wiki

## Candidate conditions (all must be true)

| # | Condition |
| --- | --- |
| 1 | Price is within D1 low + 20% of D1 range |
| 2 | 3–5 consecutive red M20 candles, last one has a lower wick |
| 3 | The candle immediately after the red streak is green |

## Repository layout

```text
fscanner/
├── .github/
│   ├── actions/git-push/        # reusable commit-and-push composite action
│   ├── dependabot.yml           # weekly uv (Mon) and Actions (Fri) updates
│   └── workflows/
│       ├── qa.yml               # ruff lint + format + ty type-check (push / PR)
│       └── scanner.yml          # twice-daily scan (08:00 + 20:00 UTC) with manual dispatch
├── fscanner/
│   ├── templates/
│   │   ├── wiki_scan.md         # template for per-run wiki pages
│   │   └── wiki_home.md         # template for wiki Home.md (created on first run)
│   ├── __init__.py
│   └── scanner.py               # all scanner logic
├── wiki/                        # cloned at runtime by GitHub Actions (not in git)
├── pyproject.toml
└── uv.lock
```

## Setup

### What you need (all free)

| Service | Purpose |
| --- | --- |
| GitHub | hosts code, runs the scanner, serves this page |
| Twelve Data | forex OHLCV API |
| Telegram | receives alerts on your phone (optional) |

### 1 — Add GitHub Secrets

Go to **Settings → Secrets and variables → Actions → New repository secret** and add:

| Secret | Required | Where to get it |
| --- | --- | --- |
| `TWELVE_DATA_KEY` | yes | [twelvedata.com](https://twelvedata.com) → dashboard → API key |
| `TELEGRAM_TOKEN` | no | Telegram → @BotFather → `/newbot` |
| `TELEGRAM_CHAT_ID` | no | Send any message to your bot, then call `https://api.telegram.org/bot<TOKEN>/getUpdates` and read `result[0].message.chat.id` |

Telegram is optional — if either variable is missing the scanner skips alerts but still writes results to the wiki.

### 2 — Enable the Wiki

**Settings → Features → Wikis** — must be turned on before the first scan so the Actions runner can clone it.

### 3 — Trigger a first run

Go to **Actions → Forex Scanner → Run workflow**.

The pipeline runs QA (lint + type-check) first. If QA passes, the scanner runs and commits results to the wiki.

## API usage

The free Twelve Data plan allows **8 credits/minute** and **800 credits/day**. The scanner makes one API call per symbol with an 8-second gap between calls.

A full 49-pair D1 scan uses 49 credits (~6.5 min). If pairs are in the hot zone, each gets one additional M20 call. Running twice a day uses ~100–150 credits total, well within the 800 daily free-tier limit.

## Local development

```bash
# Install dependencies
uv sync

# Run QA (lint + format check + type check)
uv run ruff check fscanner/
uv run ruff format --check fscanner/
uv run ty check fscanner/

# Run tests
uv run pytest

# Run the scanner (loads credentials from .env)
uv run --env-file .env fscanner
```

## Tests

Tests live in `tests/test_scanner.py` and cover all pure-logic functions in `fscanner/scanner.py`. External I/O (HTTP calls, file writes) is patched with `monkeypatch` and `unittest.mock`.

| Area | What is tested |
| --- | --- |
| `_require_env` | present, missing, empty-string |
| `_parse_candle` | string-to-float coercion, pass-through floats |
| `in_hot_zone` | at low, at 20% boundary, above zone, below low, zero-range candle, pct rounding |
| `is_red` / `is_green` | bearish, bullish, doji (equal close = green) |
| `has_lower_wick` | exact 10% threshold, below threshold, zero-range candle, custom `min_wick_pct`, green body |
| `find_pattern` | streaks of 3, 4, 5; longest-streak preference; no wick on last red; too few candles; non-contiguous streak; return field correctness |
| `get_d1` / `get_m20` | successful response, API error, empty response, too few values |
| `_candidates_section` | empty list, single candidate, chart-link toggle |
| `_all_pairs_table` | error row, candidate row, non-candidate row |
| `build_wiki_page` | filename format, timestamp, candidate count |
| `update_wiki_index` | inserts row, prepends before existing rows, creates `Home.md` from template |
| `save_wiki` | skips gracefully when wiki dir absent, writes page file |
| `api_get` | success path, all-attempts exhausted, 429 retry loop |

The `.env` file is gitignored. Copy the example and fill in your key:

```bash
TWELVE_DATA_KEY=your_key_here

# Optional — leave blank to disable Telegram alerts
TELEGRAM_TOKEN=
TELEGRAM_CHAT_ID=
```
