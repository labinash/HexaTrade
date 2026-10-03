# HexTrade

NIFTY and SENSEX option-chain analysis with paper trading and an **alert-only** notification engine. Signals are analytical scores, not guaranteed predictions. The app does not place live broker orders from alerts.

## Run

1. SQL Server database `HexTrade` (trusted connection by default). Copy env values into `backend/.env` — never commit secrets.
2. Create alert tables (once):

```powershell
cd backend
$env:PYTHONPATH = (Get-Location).Path
python scripts/apply_012.py
```

3. API (port 8000):

```powershell
cd backend
$env:PYTHONPATH = (Get-Location).Path
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

4. UI (port 5999, proxies `/api` to 8000):

```powershell
npm run dev
```

Open [http://127.0.0.1:5999/alerts](http://127.0.0.1:5999/alerts). Connect Zerodha from **Broker** if you want live ticks. Paper trading remains a separate page and still never sends Kite orders unless you explicitly arm live trading elsewhere.

## Alert engine

The market loop (every ~2s) scores NIFTY and SENSEX using the same live candles, VWAP, EMA, RSI, volume, option-chain candidates, and implied direction already used by the dashboard. Extra alert helpers (EMA 9/21, ATR Supertrend, swing S/R) reuse `domain/indicators.py` — they do not replace the strategy engine.

| Output | Meaning |
| --- | --- |
| CE BUY | Bullish tape, VWAP/resistance, EMA/momentum, volume, liquidity, and R:R all clear, score ≥ threshold |
| PE BUY | Bearish tape with the matching confirmation set |
| WAIT | Sideways, stale/missing data, outside hours, wide spread, thin liquidity, scores too close, cooldown, or disabled filter |

Default mode is **alert-only**. A BUY row is stored and notified; nothing calls a broker `place_order` API and nothing opens a paper trade from this engine.

### Configuration (`alert_setting`, Alerts page)

| Field | Default | Role |
| --- | --- | --- |
| `notifications_enabled` | on | Master switch; off skips storing BUY alerts |
| `sound_enabled` | off | Short beep on a new BUY |
| `browser_notify` | off | Browser Notification API (permission required) |
| `nifty_enabled` / `sensex_enabled` | on | Instrument filters |
| `ce_alerts` / `pe_alerts` | on | Option-side filters |
| `min_confidence` | 70 | Minimum CE or PE score for BUY |
| `min_score_separation` | 8 | WAIT if both scores are ≥ threshold and this close |
| `cooldown_seconds` | 300 | Suppress duplicate same-side/strike BUY |
| `max_spread_percent` | 2 | Reject wide bid/ask |
| `min_option_volume` / `min_option_oi` | 100 / 500 | Liquidity floor |
| `min_rr` | 1.5 | Minimum R:R using configured SL / T1 |
| `sl_percent` | 20 | Option-premium stop from entry LTP |
| `target1_rr` / `target2_rr` | 1.5 / 2.5 | Targets as multiples of risk |
| `supertrend_atr_mult` | 3 | Supertrend band width |
| `time_start` / `time_end` | 09:20 / 15:10 IST | No signals outside this window |

Lifecycle of a stored BUY: `NEW` → `ACTIVE` → `TARGET 1 HIT` / `TARGET 2 HIT` / `STOP-LOSS HIT` / `EXPIRED` / `CANCELLED`.

## Tests

```powershell
cd backend
$env:PYTHONPATH = (Get-Location).Path
python -m pytest tests/test_alert_engine.py -q
```

Coverage includes CE BUY, PE BUY, WAIT (sideways, stale, hours, spread, liquidity, volume, filters), duplicate cooldown, and lifecycle transitions.
