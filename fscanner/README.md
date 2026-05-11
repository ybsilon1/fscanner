# Forex Scanner — Setup Guide

Zero hardware needed. Runs free on GitHub.

## What you need (all free)

| Service | What for | Sign up |
|---|---|---|
| GitHub | hosts code, runs scanner, serves dashboard | github.com |
| Twelve Data | forex price API | twelvedata.com |
| Telegram | receive alerts on phone | telegram.org |

## File structure
```
forex-scanner/
├── scanner.py
├── .github/workflows/scanner.yml
└── docs/index.html
```

## Step 1 — Create GitHub repo
Go to github.com → New repository → name it `forex-scanner` → Public → Create.

## Step 2 — Upload files
Upload scanner.py, .github/workflows/scanner.yml, docs/index.html keeping the folder structure.

## Step 3 — Get Twelve Data API key
Sign up at twelvedata.com → copy your API Key from the dashboard.

> NOTE: Free plan = 800 req/day. Full 50-pair scan uses ~100 req/run.
> Hourly = 2,400/day which exceeds the free limit.
> Either upgrade, or reduce PAIRS in scanner.py to the top 20 (see below).

## Step 4 — Create Telegram bot
1. Open Telegram → search @BotFather → /newbot → copy the bot token
2. Start a chat with your bot
3. Visit: https://api.telegram.org/botTOKEN/getUpdates
4. Find "id" inside "chat" — that is your chat ID

## Step 5 — Add GitHub Secrets
Settings → Secrets and variables → Actions → New repository secret:
- TWELVE_DATA_KEY
- TELEGRAM_TOKEN
- TELEGRAM_CHAT_ID

## Step 6 — Enable GitHub Pages
Settings → Pages → Source: Deploy from branch → Branch: main, Folder: /docs → Save.
Dashboard will be at: https://YOUR_USERNAME.github.io/forex-scanner

## Step 7 — Run manually first
Actions tab → Forex Scanner → Run workflow → watch the logs.

## Reduce to 20 pairs (free API tier)
Edit PAIRS in scanner.py:
```python
PAIRS = [
    "EUR/USD","USD/JPY","GBP/USD","USD/CAD","AUD/USD",
    "USD/CHF","NZD/USD","EUR/GBP","EUR/JPY","GBP/JPY",
    "AUD/JPY","EUR/CHF","GBP/CHF","EUR/AUD","EUR/CAD",
    "AUD/CAD","AUD/NZD","GBP/AUD","GBP/CAD","CAD/JPY",
]
```

## Scanner logic
```
For each pair:
  1. Get yesterday's D1 candle
  2. Hot zone = D1 low + 20% of range
  3. If current price not in zone → skip
  4. Get last 12 M20 candles
  5. Pattern: most recent candle GREEN,
     preceded by 3-5 RED candles,
     last red has a LOWER WICK (>=10% of range)
  6. Match → Telegram alert + chart image
  7. All results → docs/status.json (GitHub Pages)
```
