# Forex Scanner

Scans 50 forex pairs every hour via GitHub Actions and sends a Telegram alert when a setup matches. Per-run results are published to the [Wiki](../../wiki). Zero hardware needed — runs free on GitHub.

## How it works

For each pair the scanner:

1. Fetches yesterday's completed **D1 candle** from Twelve Data
2. Checks whether the current price is in the **bottom 20% of the D1 range** (the "hot zone")
3. If yes, fetches the last 12 **M20 candles** and looks for the pattern:
   - 3–5 consecutive **red candles**, the last of which has a **lower wick** (≥ 10% of its range)
   - followed immediately by a **green candle**
4. On a match: sends a **Telegram alert** with a chart image and records the pair as a candidate
5. Writes `docs/status.json`, regenerates `docs/index.md`, and pushes a dated page to the Wiki

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
│   └── workflows/
│       ├── qa.yml               # ruff lint + format + ty type-check (push / PR)
│       └── scanner.yml          # hourly scan — runs qa as a gate, then scans
├── docs/                        # GitHub Pages source
│   └── index.md                 # app description (served at your Pages URL)
├── fscanner/
│   ├── templates/
│   │   ├── dashboard.md         # template for docs/index.md (daily report)
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
| Telegram | receives alerts on your phone |

### 1 — Add GitHub Secrets

Go to **Settings → Secrets and variables → Actions → New repository secret** and add:

| Secret | Where to get it |
| --- | --- |
| `TWELVE_DATA_KEY` | [twelvedata.com](https://twelvedata.com) → dashboard → API key |
| `TELEGRAM_TOKEN` | Telegram → @BotFather → `/newbot` |
| `TELEGRAM_CHAT_ID` | Send any message to your bot, then call `https://api.telegram.org/bot<TOKEN>/getUpdates` and read `result[0].message.chat.id` |

### 2 — Enable GitHub Pages

**Settings → Pages → Source: Deploy from branch → Branch: `main`, Folder: `/docs`**

### 3 — Enable the Wiki

**Settings → Features → Wikis** — must be turned on before the first scan so the Actions runner can clone it.

### 4 — Trigger a first run

Go to **Actions → Forex Scanner → Run workflow**.

The pipeline runs QA (lint + type-check) first. If QA passes, the scanner runs and commits results to `docs/` and the wiki.

## API usage

The free Twelve Data plan allows **800 requests/day**. A full 50-pair scan uses ~150 requests (3 calls per pair). Running hourly = ~3,600 requests/day, which exceeds the free limit.

Options:

- **Upgrade** to a paid Twelve Data plan
- **Reduce the pair list** in [fscanner/scanner.py](fscanner/scanner.py) — 26 pairs fits within the free quota at hourly frequency

## Local development

```bash
# Install dependencies
uv sync

# Run QA (lint + format check + type check)
uv run ruff check fscanner/
uv run ruff format --check fscanner/
uv run ty check fscanner/

# Run the scanner (requires env vars)
TWELVE_DATA_KEY=... TELEGRAM_TOKEN=... TELEGRAM_CHAT_ID=... uv run fscanner
```
