"""Unit tests for fscanner.scanner."""

import datetime
import time
from unittest.mock import MagicMock, patch

import pytest

import fscanner.scanner as scanner


# ── Fixtures ───────────────────────────────────────────────────────────────────


def make_candle(
    dt: str = "2026-05-11 00:00:00",
    open_: float = 1.1000,
    high: float = 1.1100,
    low: float = 1.0900,
    close: float = 1.1050,
) -> dict:
    return {"datetime": dt, "open": open_, "high": high, "low": low, "close": close}


def red(dt: str = "2026-05-11 10:00:00", wick: bool = False) -> dict:
    """Red candle; optionally with a lower wick ≥ 10% of range."""
    if wick:
        # range = 0.0100, body_bottom = close = 1.0960, wick = 1.0960-1.0950 = 0.0010 = 10%
        return make_candle(dt, open_=1.1000, high=1.1010, low=1.0950, close=1.0960)
    # range = 0.0200, body_bottom = close = 1.0990, wick = 1.0990-1.0990 = 0 → no lower wick
    return make_candle(dt, open_=1.1000, high=1.1100, low=1.0990, close=1.0990)


def green(dt: str = "2026-05-11 10:20:00") -> dict:
    return make_candle(dt, open_=1.0995, high=1.1050, low=1.0990, close=1.1040)


# ── _require_env ───────────────────────────────────────────────────────────────


def test_require_env_present(monkeypatch):
    monkeypatch.setenv("TEST_KEY", "abc123")
    assert scanner._require_env("TEST_KEY") == "abc123"


def test_require_env_missing(monkeypatch):
    monkeypatch.delenv("TEST_KEY", raising=False)
    with pytest.raises(RuntimeError, match="Missing required env var: TEST_KEY"):
        scanner._require_env("TEST_KEY")


def test_require_env_empty_string(monkeypatch):
    monkeypatch.setenv("TEST_KEY", "")
    with pytest.raises(RuntimeError):
        scanner._require_env("TEST_KEY")


# ── _parse_candle ──────────────────────────────────────────────────────────────


def test_parse_candle_converts_strings():
    raw = {
        "datetime": "2026-05-11 00:00:00",
        "open": "1.1000",
        "high": "1.1100",
        "low": "1.0900",
        "close": "1.1050",
    }
    c = scanner._parse_candle(raw)
    assert c["datetime"] == "2026-05-11 00:00:00"
    assert c["open"] == 1.1000
    assert c["high"] == 1.1100
    assert c["low"] == 1.0900
    assert c["close"] == 1.1050


def test_parse_candle_already_floats():
    raw = {"datetime": "2026-05-11", "open": 1.0, "high": 2.0, "low": 0.5, "close": 1.5}
    c = scanner._parse_candle(raw)
    assert isinstance(c["open"], float)
    assert c["high"] == 2.0


# ── in_hot_zone ────────────────────────────────────────────────────────────────


def test_in_hot_zone_at_low():
    d1 = make_candle(low=1.0900, high=1.1100)
    in_zone, pct = scanner.in_hot_zone(1.0900, d1)
    assert in_zone is True
    assert pct == 0.0


def test_in_hot_zone_exactly_at_zone_top():
    d1 = make_candle(low=1.0000, high=1.1000)  # range = 0.1000, zone_top = 1.0200
    in_zone, pct = scanner.in_hot_zone(1.0200, d1)
    assert in_zone is True
    assert pct == 20.0


def test_in_hot_zone_above_zone():
    d1 = make_candle(low=1.0000, high=1.1000)
    in_zone, pct = scanner.in_hot_zone(1.0201, d1)
    assert in_zone is False
    assert pct == pytest.approx(20.1, abs=0.05)


def test_in_hot_zone_below_low():
    d1 = make_candle(low=1.0000, high=1.1000)
    in_zone, pct = scanner.in_hot_zone(0.9999, d1)
    assert in_zone is False


def test_in_hot_zone_zero_range():
    d1 = make_candle(low=1.0500, high=1.0500)
    in_zone, pct = scanner.in_hot_zone(1.0500, d1)
    assert in_zone is False
    assert pct == 0.0


def test_in_hot_zone_pct_rounds_to_one_decimal():
    d1 = make_candle(low=1.0000, high=1.1000)  # range = 0.1000
    _, pct = scanner.in_hot_zone(1.0055, d1)  # 5.5%
    assert pct == 5.5


# ── is_red / is_green ─────────────────────────────────────────────────────────


def test_is_red_true():
    assert scanner.is_red(make_candle(open_=1.10, close=1.09)) is True


def test_is_red_false_when_equal():
    assert scanner.is_red(make_candle(open_=1.10, close=1.10)) is False


def test_is_green_true_equal():
    assert scanner.is_green(make_candle(open_=1.10, close=1.10)) is True


def test_is_green_true_higher():
    assert scanner.is_green(make_candle(open_=1.09, close=1.10)) is True


def test_is_green_false():
    assert scanner.is_green(make_candle(open_=1.10, close=1.09)) is False


# ── has_lower_wick ─────────────────────────────────────────────────────────────


def test_has_lower_wick_exact_10_pct():
    # range=0.0100, body_bottom=close=1.0960, wick=1.0960-1.0950=0.0010 → 10%
    c = make_candle(open_=1.1000, high=1.1010, low=1.0950, close=1.0960)
    assert scanner.has_lower_wick(c) is True


def test_has_lower_wick_below_threshold():
    # range = high-low = 1.1100-1.0900 = 0.0200
    # body_bottom = min(open, close) = min(1.1000, 1.1050) = 1.1000
    # wick = 1.1000 - 1.0990 = 0.0010; pct = 0.0010/0.0200 = 5% → below 10% threshold
    c = make_candle(open_=1.1000, high=1.1100, low=1.0990, close=1.1050)
    assert scanner.has_lower_wick(c) is False


def test_has_lower_wick_zero_range():
    c = make_candle(open_=1.10, high=1.10, low=1.10, close=1.10)
    assert scanner.has_lower_wick(c) is False


def test_has_lower_wick_custom_min_pct():
    # wick = 5%, custom threshold = 0.05 → passes
    c = make_candle(open_=1.1000, high=1.1010, low=1.0995, close=1.1000)
    assert scanner.has_lower_wick(c, min_wick_pct=0.05) is True


def test_has_lower_wick_green_candle():
    # green candle: body_bottom = open
    c = make_candle(open_=1.0950, high=1.1010, low=1.0900, close=1.1000)
    # range=0.0110, wick=1.0950-1.0900=0.0050, pct=0.0050/0.0110 ≈ 45.5%
    assert scanner.has_lower_wick(c) is True


# ── find_pattern ───────────────────────────────────────────────────────────────


def _dts(n: int) -> list[str]:
    """Generate n sequential datetime strings 20 minutes apart."""
    base = datetime.datetime(2026, 5, 11, 8, 0)
    return [
        (base + datetime.timedelta(minutes=20 * i)).strftime("%Y-%m-%d %H:%M:%S") for i in range(n)
    ]


def make_m20_with_pattern(streak_len: int) -> list[dict]:
    """Return m20 list ending with <streak_len> reds (last with wick) + 1 green."""
    dts = _dts(streak_len + 1)
    candles = []
    for i in range(streak_len - 1):
        candles.append(red(dts[i]))
    candles.append(red(dts[streak_len - 1], wick=True))
    candles.append(green(dts[streak_len]))
    return candles


def test_find_pattern_3_red_green():
    m20 = make_m20_with_pattern(3)
    result = scanner.find_pattern(m20)
    assert result is not None
    assert result["streak_len"] == 3


def test_find_pattern_4_red_green():
    m20 = make_m20_with_pattern(4)
    result = scanner.find_pattern(m20)
    assert result is not None
    assert result["streak_len"] == 4


def test_find_pattern_5_red_green():
    m20 = make_m20_with_pattern(5)
    result = scanner.find_pattern(m20)
    assert result is not None
    assert result["streak_len"] == 5


def test_find_pattern_prefers_longest():
    # 5 reds (last with wick) + green — both 3-streak and 5-streak match; expect 5
    dts = _dts(6)
    candles = [red(dts[i], wick=(i == 4)) for i in range(5)]
    candles.append(green(dts[5]))
    result = scanner.find_pattern(candles)
    assert result is not None
    assert result["streak_len"] == 5


def test_find_pattern_last_candle_not_green():
    m20 = make_m20_with_pattern(3)
    m20[-1]["close"] = m20[-1]["open"] - 0.001  # make last candle red
    assert scanner.find_pattern(m20) is None


def test_find_pattern_no_lower_wick_on_last_red():
    dts = _dts(4)
    # 3 plain reds (no wick) + green
    candles = [red(dts[i], wick=False) for i in range(3)]
    candles.append(green(dts[3]))
    assert scanner.find_pattern(candles) is None


def test_find_pattern_too_short():
    m20 = make_m20_with_pattern(3)[:3]  # only 3 candles, need ≥ 4
    assert scanner.find_pattern(m20) is None


def test_find_pattern_returns_correct_fields():
    m20 = make_m20_with_pattern(3)
    result = scanner.find_pattern(m20)
    assert "streak_len" in result
    assert "streak_start" in result
    assert "last_red" in result
    assert "green_candle" in result
    assert result["green_candle"] == m20[-1]
    assert result["last_red"] == m20[-2]


def test_find_pattern_streak_start_datetime():
    m20 = make_m20_with_pattern(3)
    result = scanner.find_pattern(m20)
    assert result["streak_start"] == m20[0]["datetime"]


def test_find_pattern_mixed_streak_not_all_red():
    # green in the middle of streak → no pattern
    dts = _dts(5)
    candles = [red(dts[0]), green(dts[1]), red(dts[2], wick=True), green(dts[3])]
    assert scanner.find_pattern(candles) is None


# ── get_d1 / get_m20 ──────────────────────────────────────────────────────────


def test_get_d1_returns_tuple(monkeypatch):
    monkeypatch.setattr(
        scanner,
        "api_get",
        lambda *a, **kw: {
            "status": "ok",
            "values": [
                {
                    "datetime": "2026-05-12",
                    "open": "1.1",
                    "high": "1.12",
                    "low": "1.09",
                    "close": "1.105",
                },
                {
                    "datetime": "2026-05-11",
                    "open": "1.09",
                    "high": "1.11",
                    "low": "1.08",
                    "close": "1.10",
                },
            ],
        },
    )
    result = scanner.get_d1("EUR/USD")
    assert result is not None
    d1, price = result
    assert price == 1.105
    assert d1["datetime"] == "2026-05-11"


def test_get_d1_returns_none_on_error(monkeypatch):
    monkeypatch.setattr(scanner, "api_get", lambda *a, **kw: {"status": "error", "message": "bad"})
    assert scanner.get_d1("EUR/USD") is None


def test_get_d1_returns_none_on_empty(monkeypatch):
    monkeypatch.setattr(scanner, "api_get", lambda *a, **kw: {})
    assert scanner.get_d1("EUR/USD") is None


def test_get_d1_returns_none_if_only_one_value(monkeypatch):
    monkeypatch.setattr(
        scanner,
        "api_get",
        lambda *a, **kw: {
            "values": [
                {
                    "datetime": "2026-05-12",
                    "open": "1.1",
                    "high": "1.12",
                    "low": "1.09",
                    "close": "1.105",
                }
            ]
        },
    )
    assert scanner.get_d1("EUR/USD") is None


def test_get_m20_returns_list(monkeypatch):
    raw_values = [
        {
            "datetime": f"2026-05-11 0{i}:00:00",
            "open": "1.1",
            "high": "1.11",
            "low": "1.09",
            "close": "1.105",
        }
        for i in range(13)
    ]
    monkeypatch.setattr(scanner, "api_get", lambda *a, **kw: {"values": raw_values})
    result = scanner.get_m20("EUR/USD")
    assert result is not None
    assert len(result) == 12  # [1:] reversed, excludes current candle


def test_get_m20_returns_none_on_error(monkeypatch):
    monkeypatch.setattr(scanner, "api_get", lambda *a, **kw: {"status": "error"})
    assert scanner.get_m20("EUR/USD") is None


def test_get_m20_returns_none_if_too_few(monkeypatch):
    monkeypatch.setattr(
        scanner,
        "api_get",
        lambda *a, **kw: {
            "values": [
                {
                    "datetime": "2026-05-11",
                    "open": "1.1",
                    "high": "1.11",
                    "low": "1.09",
                    "close": "1.1",
                }
            ]
        },
    )
    assert scanner.get_m20("EUR/USD") is None


# ── _candidates_section ────────────────────────────────────────────────────────


def test_candidates_section_empty():
    out = scanner._candidates_section([], include_chart=False)
    assert "No candidates" in out


def test_candidates_section_with_candidate():
    candidate = {
        "symbol": "EUR/USD",
        "price": 1.09500,
        "pct_from_low": 5.0,
        "d1": make_candle(low=1.0900, high=1.1100),
        "pattern": {"streak_len": 3},
    }
    out = scanner._candidates_section([candidate], include_chart=False)
    assert "EUR/USD" in out
    assert "1.09500" in out
    assert "5.0%" in out
    assert "3 red" in out


def test_candidates_section_with_chart():
    candidate = {
        "symbol": "EUR/USD",
        "price": 1.09500,
        "pct_from_low": 5.0,
        "d1": make_candle(low=1.0900, high=1.1100),
        "pattern": {"streak_len": 3},
    }
    out = scanner._candidates_section([candidate], include_chart=True)
    assert "chart_EURUSD.png" in out


# ── _all_pairs_table ──────────────────────────────────────────────────────────


def test_all_pairs_table_error_row():
    results = [{"symbol": "EUR/USD", "error": "timeout", "candidate": False}]
    lines = scanner._all_pairs_table(results)
    joined = "\n".join(lines)
    assert "error: timeout" in joined


def test_all_pairs_table_candidate_row():
    results = [
        {
            "symbol": "EUR/USD",
            "candidate": True,
            "error": None,
            "price": 1.09500,
            "pct_from_low": 5.0,
            "pattern": {"streak_len": 3},
        }
    ]
    lines = scanner._all_pairs_table(results)
    joined = "\n".join(lines)
    assert "**candidate**" in joined
    assert "3R+G" in joined
    assert "in zone" in joined


def test_all_pairs_table_no_pattern():
    results = [
        {
            "symbol": "USD/JPY",
            "candidate": False,
            "error": None,
            "price": 154.500,
            "pct_from_low": 30.0,
            "pattern": None,
        }
    ]
    lines = scanner._all_pairs_table(results)
    joined = "\n".join(lines)
    assert "USD/JPY" in joined
    # Not in zone (30%) and no pattern
    assert "**candidate**" not in joined


# ── build_wiki_page ───────────────────────────────────────────────────────────


def test_build_wiki_page_filename_format():
    run_dt = datetime.datetime(2026, 5, 12, 9, 10, tzinfo=datetime.timezone.utc)
    results = [
        {
            "symbol": "EUR/USD",
            "candidate": False,
            "error": None,
            "price": 1.09,
            "pct_from_low": 30.0,
            "pattern": None,
        }
    ]
    page_name, content = scanner.build_wiki_page(results, run_dt)
    assert page_name == "Scan-2026-05-12-0910"


def test_build_wiki_page_contains_timestamp():
    run_dt = datetime.datetime(2026, 5, 12, 9, 10, tzinfo=datetime.timezone.utc)
    results = [
        {
            "symbol": "EUR/USD",
            "candidate": False,
            "error": None,
            "price": 1.09,
            "pct_from_low": 30.0,
            "pattern": None,
        }
    ]
    _, content = scanner.build_wiki_page(results, run_dt)
    assert "2026-05-12 09:10 UTC" in content


def test_build_wiki_page_candidate_count():
    run_dt = datetime.datetime(2026, 5, 12, 9, 10, tzinfo=datetime.timezone.utc)
    candidate = {
        "symbol": "EUR/USD",
        "candidate": True,
        "error": None,
        "price": 1.09500,
        "pct_from_low": 5.0,
        "d1": make_candle(low=1.0900, high=1.1100),
        "pattern": {"streak_len": 3},
    }
    non_candidate = {
        "symbol": "USD/JPY",
        "candidate": False,
        "error": None,
        "price": 154.0,
        "pct_from_low": 50.0,
        "pattern": None,
    }
    _, content = scanner.build_wiki_page([candidate, non_candidate], run_dt)
    assert "1 candidate" in content


# ── update_wiki_index ─────────────────────────────────────────────────────────


def test_update_wiki_index_creates_row(tmp_path):
    home = tmp_path / "Home.md"
    home.write_text(
        "# Forex Scanner — Scan History\n\n| Run | Candidates | Pairs scanned |\n| --- | --- | --- |\n"
    )
    run_dt = datetime.datetime(2026, 5, 12, 9, 10, tzinfo=datetime.timezone.utc)
    scanner.update_wiki_index(tmp_path, "Scan-2026-05-12-0910", run_dt, 2, 49)
    content = home.read_text()
    assert "Scan-2026-05-12-0910" in content
    assert "| 2 | 49 |" in content


def test_update_wiki_index_prepends_row(tmp_path):
    home = tmp_path / "Home.md"
    existing_row = "| [2026-05-11 08:00 UTC](Scan-old) | 0 | 49 |"
    home.write_text(
        f"# Header\n\n| Run | Candidates | Pairs scanned |\n| --- | --- | --- |\n{existing_row}\n"
    )
    run_dt = datetime.datetime(2026, 5, 12, 9, 10, tzinfo=datetime.timezone.utc)
    scanner.update_wiki_index(tmp_path, "Scan-2026-05-12-0910", run_dt, 1, 49)
    content = home.read_text()
    new_idx = content.index("Scan-2026-05-12-0910")
    old_idx = content.index("Scan-old")
    assert new_idx < old_idx


def test_update_wiki_index_uses_template_when_no_home(tmp_path):
    run_dt = datetime.datetime(2026, 5, 12, 9, 10, tzinfo=datetime.timezone.utc)
    scanner.update_wiki_index(tmp_path, "Scan-2026-05-12-0910", run_dt, 0, 49)
    home = tmp_path / "Home.md"
    assert home.exists()
    assert "Scan-2026-05-12-0910" in home.read_text()


# ── save_wiki ──────────────────────────────────────────────────────────────────


def test_save_wiki_skips_when_no_wiki_dir(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(scanner, "_REPO_ROOT", tmp_path / "nonexistent")
    run_dt = datetime.datetime(2026, 5, 12, 9, 10, tzinfo=datetime.timezone.utc)
    scanner.save_wiki([], run_dt)  # should not raise
    out = capsys.readouterr().out
    assert "skipping" in out


def test_save_wiki_writes_page(tmp_path, monkeypatch):
    wiki_dir = tmp_path / "wiki"
    wiki_dir.mkdir()
    # Seed Home.md so update_wiki_index can insert the header
    (wiki_dir / "Home.md").write_text(
        "# Forex Scanner — Scan History\n\n| Run | Candidates | Pairs scanned |\n| --- | --- | --- |\n"
    )
    monkeypatch.setattr(scanner, "_REPO_ROOT", tmp_path)
    run_dt = datetime.datetime(2026, 5, 12, 9, 10, tzinfo=datetime.timezone.utc)
    results = [
        {
            "symbol": "EUR/USD",
            "candidate": False,
            "error": None,
            "price": 1.09,
            "pct_from_low": 30.0,
            "pattern": None,
        }
    ]
    scanner.save_wiki(results, run_dt)
    assert (wiki_dir / "Scan-2026-05-12-0910.md").exists()


# ── _rate_limit_wait (sliding-window guard) ────────────────────────────────────


def test_rate_limit_wait_passes_when_window_empty(monkeypatch):
    monkeypatch.setattr(scanner, "_call_times", [])
    scanner._rate_limit_wait()  # must return immediately without sleeping


def test_rate_limit_wait_passes_when_under_limit(monkeypatch):
    now = time.monotonic()
    monkeypatch.setattr(scanner, "_call_times", [now - 30, now - 20])  # 2 calls, limit is 7
    scanner._rate_limit_wait()  # must not sleep


def test_rate_limit_wait_blocks_when_window_full(monkeypatch):
    now = time.monotonic()
    # Fill the window with _RATE_LIMIT calls all within the last 60 s
    monkeypatch.setattr(scanner, "_call_times", [now - 50 + i for i in range(scanner._RATE_LIMIT)])
    slept: list[float] = []
    monkeypatch.setattr(time, "sleep", lambda s: slept.append(s))
    # After sleeping the oldest entry will be evicted — patch monotonic to advance time
    original_monotonic = time.monotonic
    calls = [0]

    def fake_monotonic():
        calls[0] += 1
        # On the second call inside the loop return a time far enough ahead
        return original_monotonic() + (61 if calls[0] > 2 else 0)

    monkeypatch.setattr(time, "monotonic", fake_monotonic)
    scanner._rate_limit_wait()
    assert len(slept) >= 1


def test_rate_limit_wait_evicts_old_entries(monkeypatch):
    now = time.monotonic()
    # Two calls older than 60 s ago — should be evicted, window considered empty
    monkeypatch.setattr(scanner, "_call_times", [now - 61, now - 62])
    scanner._rate_limit_wait()
    assert scanner._call_times == []


# ── api_get (rate-limit / retry logic) ────────────────────────────────────────


def test_api_get_returns_data_on_success(monkeypatch):
    mock_resp = MagicMock()
    mock_resp.json.return_value = {"status": "ok", "values": []}
    mock_resp.raise_for_status = MagicMock()

    monkeypatch.setattr(scanner, "TWELVE_DATA_KEY", "test_key")
    monkeypatch.setattr(scanner, "_call_times", [])
    with patch("fscanner.scanner.requests.get", return_value=mock_resp) as mock_get:
        result = scanner.api_get("time_series", {"symbol": "EUR/USD"})
    assert result == {"status": "ok", "values": []}
    assert mock_get.call_args[0][0] == "https://api.twelvedata.com/time_series"


def test_api_get_records_call_timestamp(monkeypatch):
    mock_resp = MagicMock()
    mock_resp.json.return_value = {"status": "ok"}
    mock_resp.raise_for_status = MagicMock()

    monkeypatch.setattr(scanner, "TWELVE_DATA_KEY", "test_key")
    monkeypatch.setattr(scanner, "_call_times", [])
    with patch("fscanner.scanner.requests.get", return_value=mock_resp):
        scanner.api_get("time_series", {"symbol": "EUR/USD"})
    assert len(scanner._call_times) == 1


def test_api_get_returns_empty_after_all_attempts_fail(monkeypatch):
    monkeypatch.setattr(scanner, "TWELVE_DATA_KEY", "test_key")
    monkeypatch.setattr(scanner, "_call_times", [])
    with patch("fscanner.scanner.requests.get", side_effect=Exception("timeout")):
        with patch("fscanner.scanner.time.sleep"):
            result = scanner.api_get("time_series", {"symbol": "EUR/USD"})
    assert result == {}


def test_api_get_handles_429(monkeypatch):
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()

    call_count = 0

    def fake_json():
        nonlocal call_count
        call_count += 1
        if call_count < 3:
            return {"code": 429}
        return {"status": "ok"}

    mock_resp.json = fake_json
    monkeypatch.setattr(scanner, "TWELVE_DATA_KEY", "test_key")
    monkeypatch.setattr(scanner, "_call_times", [])
    with patch("fscanner.scanner.requests.get", return_value=mock_resp):
        with patch("fscanner.scanner.time.sleep"):
            result = scanner.api_get("time_series", {"symbol": "EUR/USD"})
    assert result == {"status": "ok"}
