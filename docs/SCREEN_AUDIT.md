# HexTrade screen audit

Read-only audit. No source was modified. Verification: API on `127.0.0.1:8000` and frontend HTML on `:5999`, Saturday **2026-10-03** (market closed). Browser click-through was not available; each screen was exercised through its HTTP API and by reading the route → component → service path.

App context checked:

| Claim | Evidence |
|---|---|
| Paper/alerts only (banner) | Present on Dashboard, Chart, Alerts, Paper, Calendar, Backdated. **Missing** on Option chain, OI Scanner, Strike selection, Strategy, Option selection, Exits, Risk, Broker, Positions. |
| NIFTY + SENSEX CE/PE | Both underlyings throughout. |
| Timestamps Asia/Kolkata | Chart candles labelled IST 09:15–15:27 (last 3m bar open). Archive last 1m bar `2026-10-01T15:29:00`. **UTC leakage:** ticker `as_of=2026-10-03 12:31:05+00:00`; alert score `time` is naive UTC (`2026-10-03 12:34:12`). |
| History 2023-10-03 → 2026-10-01, ~738 sessions | `/api/historical-data` quality: NIFTY 738 / 276429 1m; SENSEX 738 / 276433 1m. |
| NIFTY weekly expiry Thu before Sept 2025, Tue from Sept 2025 | **Code uses 2024-09-01**, not Sept 2025. See bug H2. |
| Index close as futures proxy | Chart/backtest warnings present when option history missing. |

Live process: API `database=up`, Redis **down**, `trading_loop=running`, broker **connected** (`LIH238`), mode **PAPER**, `chart_live_enabled=false`, `session_open=false`.

---

## Code map (sidebar order)

| # | Screen | Route | UI files | Services / hooks | APIs | Tables R/W |
|---|---|---|---|---|---|---|
| 1 | Dashboard | `/` | `App.tsx` `Dashboard`; `DashboardCard.tsx`; `ChartTradeBrief.tsx`; `LiveZerodhaPanel.tsx`; `OptionTradingPanel.tsx` | `usePoll`; `engine.dashboard`; `option_buy.attach_dashboard_brief`; `chart_trading.dashboard_brief` | `GET /api/dashboard`; `GET /api/dashboard/options/{u}`; `GET /api/trading/dashboard-brief`; `GET /api/trading/zerodha/snapshot`; `POST /api/paper-orders`; `PUT /api/trading/live-settings` | R: `candle`, `underlying_profile`, `strategy*`, `alert_signal`, `position`, `paper_trade`, `market_historical_data`, `app_setting`. W: `position`/`order_record` via paper-orders; `app_setting.chart_live_enabled` |
| 2 | Calendar patterns | `/calendar-patterns` | `CalendarPatterns.tsx` | `calendar_analyzer`, `calendar_patterns` | `GET /api/calendar-patterns` | R: `market_historical_data`, `instrument` (listed expiries), `historical_paper_trade` (paper-by-weekday) |
| 3 | Chart | `/chart` | `MarketChart.tsx`; `ChartStrikeTrade.tsx` | `useChartQuery`; `chart_service`; `chart_trading` | `GET /api/chart`; `GET /api/paper-trading/settings`; `GET /api/trading/chart-strikes`; `POST /api/trading/zerodha/preview\|order` | R: `market_historical_data` (hist), `candle` (live), `chart_signal`, `paper_auto_setting`. W: `chart_signal`; **W live Kite** if armed |
| 4 | Alerts | `/alerts` | `AlertCenter.tsx` | `alert_engine` service + domain | `GET /api/alerts`; `PUT /api/alerts/settings`; `POST /api/alerts/{id}/acknowledge\|dismiss` | R/W: `alert_setting`, `alert_signal`, `alert_log`. R: `historical_paper_trade` (hist alerts) |
| 5 | Paper Trading | `/paper-trading` | `PaperTrading.tsx`; `IndexStrategyPanel.tsx` | `paper_engine`; `index_strategy`; `index_exits` (manage open INDEX) | `GET/PUT /api/paper-trading*`; `POST /api/paper-trading/index`; `GET /api/historical-paper` | R/W: `paper_auto_setting`, `paper_trade*`, `charge_rate` |
| 6 | Historical Data | `/historical-data` | `HistoricalData.tsx` | `historical_import`, `historical_repo` | `GET /api/historical-data`; `POST /api/historical-data/import`; job GET/resume | R/W: `market_historical_data`, `historical_import_job`, `historical_import_event` |
| 7 | Backdated Paper | `/backdated-paper` | `BackdatedPaper.tsx` | `historical_backtest`; `index_strategy`; `index_exits`; `exits.choose_exit` (option path) | `GET /api/historical-paper`; `POST /api/historical-paper/runs`; `GET .../runs/{id}` | R: `market_historical_data`. W: `historical_paper_run`, `historical_paper_trade`, `historical_condition_stat` |
| 8 | Option chain | `/chain` | `App.tsx` `Chain` | `engine.public_chain` / `chain` | `GET /api/chain` | R: `instrument`, live quote cache, `oi_reference` |
| 9 | OI Scanner | `/oi-scanner` | `OiScanner.tsx`; `OiControl.tsx`; `oiControlMath.ts` | `oi_scanner` | `GET /api/oi-scanner` | R: `instrument`, quote snapshots, `oi_scanner_config` (API exists; **UI does not call config**) |
| 10 | Strike selection | `/strike-selection` | `StrikeSelection.tsx` | `selection` + `strike_selection`; `greeks` | `GET /api/strike-selection`; `POST .../paper-trade` | R: `strike_selection_config`, chain, `strategy`. W: `position`, `strike_selection_run`, `strike_selection_candidate` |
| 11 | Strike selection config | `/strike-selection/config` | `StrikeSelection.tsx` `StrikeSelectionConfig` | same | `GET/PUT /api/strike-selection/config` | R/W: `strike_selection_config` |
| 12 | Strategy | `/strategy` | `StrategyConfig.tsx` | `mapping` / strategy version | `GET/PUT /api/strategy` | R/W: `strategy`, `strategy_version`, `strategy_parameters` |
| 13 | Option selection | `/selection` | `App.tsx` `ConfigScreen` | `mapping` | `GET /api/strategy`; `PUT /api/option-selection` | R/W: `option_selection_rule` |
| 14 | Exits | `/exits` | `App.tsx` `ConfigScreen` | `domain.exits` | `GET /api/strategy`; `PUT /api/exits` | R/W: `exit_policy` |
| 15 | Risk | `/risk` | `App.tsx` `RiskScreen` | `domain.risk`; ATR on `underlying_profile` | `GET /api/strategy`; `PUT /api/risk` | R/W: `risk_policy`, `underlying_profile.atr_threshold` |
| 16 | Broker | `/broker` | `App.tsx` `Broker` | Kite adapter; `require_db` | `/api/broker/*`; `/api/trading/mode\|arm\|kill-switch\|resume`; `POST /api/instruments/refresh` | R/W: `broker_session`, `app_setting`, `instrument` |
| 17 | Positions | `/positions` | `App.tsx` `Positions` | `engine` positions | `GET /api/positions`; `POST /api/positions/{id}/exit`; `GET /api/orders` | R/W: `position`, `order_record`, `fill` |
| — | Audit (extra) | `/audit` | `App.tsx` `Audit` | — | `GET /api/audit` | R: `audit_event` |

Shared ticker: `PaperTradeNotifications.tsx` `useTicker` → `GET /api/ticker`.

---

## Shared modules and who uses them

| Module | File | Used by |
|---|---|---|
| Indicators EMA / RSI / ATR / VWAP / ADX | `backend/app/domain/indicators.py` | Dashboard (`strategy` + `option_buy`), Chart (`chart_service.series_from_bars`), Paper/Backtest (`index_strategy.features_from_bars`), Alerts (RSI/ATR via same helpers; EMA9/21 via `alert_indicators.ema_pair`) |
| Dashboard 9-condition engine | `domain/strategy.py` `evaluate_snapshot` | Dashboard, Chart live `market_view`, Strike selection signal, original auto-entry |
| Option CE/PE 9-check | `domain/option_buy.py` + `services/option_buy.py` | Dashboard cards, option ladder |
| Index BUY/SELL groups + gates | `domain/index_strategy.py` | Paper live_decisions, Chart hist markers, Backdated walk, `validate_index_entry` |
| Alert CE/PE scorer | `domain/alert_engine.py` `score_side` | Alerts only |
| Strike score + Black-76 | `domain/strike_selection.py`, `domain/greeks.py` | Strike selection only |
| Option pick (ATM/offset) | `services/selection.py` / `domain/chain.py` | Dashboard preferred + original engine (`engine.py` ~768) |
| Exit A (`choose_exit`) | `domain/exits.py` | Option paper manage, original `position`, option path in historical walk, `domain/backtest.py` |
| Exit B (`evaluate_index_exit`) | `domain/index_exits.py` | INDEX `paper_engine._manage_open`, historical **proxy** walk |
| Risk | `domain/risk.py` `evaluate_risk` | Original engine entry, **live chart orders** (`chart_trading.py`) |
| Calendar stats | `domain/calendar_patterns.py` | Calendar only |
| Supertrend / swings | `domain/alert_indicators.py` | Alerts only |

**Four scoring families are live at once.** They do not agree. See verification and bug H3.

---

## Dead / unreachable (confirmed unused by `frontend/src`)

| Item | Evidence |
|---|---|
| `ExitAlerts` in `frontend/src/App.tsx:351` | Defined; never rendered. Dashboard uses `AlertHistory` instead. |
| `GET /api/indicators` | No frontend caller. |
| `GET /api/signals`, `GET /api/signals/current` | No frontend caller. `signal` / `signal_condition` still written by `engine.py`. |
| `POST /api/backtests`, `GET /api/backtests/{id}`, `POST /api/backtests/ingest` | No frontend caller. UI uses `/api/historical-paper`. Tables `backtest_run`, `backtest_trade` are the old path. |
| `GET/PUT /api/oi-scanner/config` | OI Control is client-side only (`oiControlMath.ts`). |
| `GET/PUT /api/settings/broker` | Broker screen uses `/api/broker/*`. |
| `GET /api/underlyings/{symbol}/expiries` | No frontend caller. |
| `GET /api/alerts/settings` | UI reads config from `GET /api/alerts`; only PUT is used. |
| `GET /api/strike-selection/runs` | No frontend caller. |
| Extra nav **Audit** | Reachable but not in the 17-screen spec. |

---

## Per-screen analysis

### 1 Dashboard — PARTIAL

**Purpose.** Session briefing: ready? trend? preferred CE/PE? paper/risk snapshot.

**What I see.** Two cards (NIFTY/SENSEX): Market Ready, trend, preferred side/contract, green indicators, CE/PE blocks, readiness. Risk card. ChartTradeBrief. LiveZerodhaPanel. Alert History. Collapsed archive/P&L/quality. Collapsed option ladder.

**What I can do.** Expand sections. Record paper buy on ladder. Enable live trading + view/sync Zerodha net positions (real book). Exit OPEN `position` rows.

**Data.** Live quotes + live `candle` table (not archive). After hours on 2026-10-03: NIFTY LTP 22421.95 (matches archive last close 2026-10-01), futures 22520, status **NOT READY**, trend NOT READY, no preferred contract. CE 33.33% / PE 22.22%. EMA 22521.85, RSI 49.24, ATR 10.36, VWAP 22501.06.

**Settings.** Strategy 9 conditions + Option selection (preferred contract) + Risk (ATR threshold, daily loss) + paper_auto (display). Live latch from Broker / this panel.

**Outputs.** Paper-orders → `position`. Live settings → `app_setting`. Does not write Alerts.

**Evidence.** `GET /api/dashboard` 200, 45 KB, 2 markets, 0 positions, 0 alert_history rows, paper net **−751.28** (18 closed), hist net **−9971** (60 trades). Banner present.

**Bugs.** H1 live-order panel on a “paper only” screen. H3 scores ≠ Alerts ≠ Paper. H5 hist P&L −9971 ≠ run 2 API −8021. Alert History empty while Alerts has 40 historical rows (different source).

---

### 2 Calendar patterns — WORKING

**Purpose.** Historical weekday / expiry-proximity / regime stats on stored INDEX sessions.

**What I see.** Filters; weekday table; weekday×month heatmap; week-of-month; market conditions; expiry table (when listed expiries exist); paper-by-weekday; year table; NIFTY vs SENSEX; ranking.

**What I can do.** All listed filters; min sample; heat metric; focus NIFTY/SENSEX; jump to Chart with highlight dates.

**Data.** Stored `market_historical_data` daily OHLC. Not mocked. Expiry from `instrument.expiry`, not weekday heuristic (`calendar_analyzer.py:219–232`). June 2024 probe: 19 sessions, 1 holiday, `expiry_analysis=available`.

**Settings.** None from Strategy/Risk.

**Outputs.** SessionStorage highlight for Chart only.

**Evidence.** `GET /api/calendar-patterns?...date_from=2024-06-01&date_to=2024-06-30` 200. Banner present. Can be slow on full archive (180s UI watchdog).

---

### 3 Chart — PARTIAL

**Purpose.** Plot index candles + indicators + paper markers; optional strike rail.

**What I can do.** Instrument, TF, mode, date range, Apply, indicator/filter/level toggles, replay, strike BUY (paper or live).

**Data.** LIVE_PAPER → live `candle`. HISTORICAL_* → archive. Timezone field `Asia/Kolkata`.

| Date | Result |
|---|---|
| LIVE 2026-10-03 Sat | `data_status=OK`, **0 candles**, no warning (empty is correct; status OK is misleading). |
| 2024-06-04 election | 125 3m bars 09:15–15:27, open 23179.5 → close 21996.45. 2 signals. Proxy warning. |
| 2025-08-28 Thu | 125 bars. Session exists. |
| 2025-09-02 Tue | 125 bars. Session exists. |
| 2026-10-03 Sat historical | **Clamped to 2026-10-01** (last stored), 125 bars, not “no session”. |
| 2024-01-26 holiday | **Snaps to 2024-01-25**, warning “Showing 2024-01-25”, `data_status=OK`. |

**Settings.** Paper index strategy for markers. Strategy params for indicator periods.

**Outputs.** `chart_signal`. Live orders if armed.

**Bugs.** H1 live rail. H4 weekend/holiday snap + Saturday clamp. Banner present but contradicts live rail.

---

### 4 Alerts — PARTIAL

**Purpose.** CE/PE BUY watches; never an order.

**What I can do.** 7 toggles, 11 numeric thresholds, time window, ack/dismiss.

**Data.** Live tape + option quotes. 2026-10-03: 0 open, 4 latest_scores all WAIT, 40 historical_alerts. Snapshots array empty. NIFTY CE 24.67 / PE 43.33; SENSEX CE 19.33 / PE 66.67. Reasons include “Outside configured trading hours” and volume 0.

**Settings.** Own `alert_setting`. RSI bands hardcoded 45–68 / 32–55 in `alert_engine.py:251–253`, not Strategy RSI.

**Outputs.** `alert_signal` / `alert_log` only.

**Bugs.** H3 score ≠ Dashboard (NIFTY CE 24.67 vs 33.33). M2 UTC-ish `time` on scores. Historical alerts still bound to old paper runs (scores often null on pre-column rows).

---

### 5 Paper Trading — PARTIAL

**Purpose.** Hypothetical index/option blotter for **today**.

**What I can do.** Mode buttons, auto toggle, index BUY/SELL, lots/limits, IndexStrategyPanel presets/fields, open a trade, start a historical range from this page too.

**Data.** `paper_trade` today: **0 trades, net 0**. Live decisions still computed: **NIFTY BUY 75 / SELL 0** (threshold 70) on Saturday after hours. Conditions in payload are the **legacy** 7-row weights (EMA 20, VWAP 15, …), not the new Trend 25 / Trigger 20 groups advertised on the panel.

**Settings.** `paper_auto_setting` including `index_strategy` JSON. Risk screen limits are **not** this book’s limits.

**Outputs.** `paper_trade*`. Does not update Positions.

**Bugs.** H3 weekend BUY. H5 book isolated. New gates (`validate_index_entry`) are not applied to the displayed `decide()` (no session HOLD).

---

### 6 Historical Data — WORKING

**Purpose.** Import and quality of the dedicated archive.

**What I can do.** Instrument BOTH/NIFTY/SENSEX, TF, from/to, INITIAL / INCREMENTAL / Resume.

**Data.** Real archive. `quality` (UI), not `coverage`. NIFTY/SENSEX 1Minute 738 days, 2023-10-03T09:15 → 2026-10-01T15:29. `option_data_note` states Kite has no expired option history.

**Outputs.** Archive tables only.

---

### 7 Backdated Paper — PARTIAL

**Purpose.** Walk stored history; optional threshold × exit sweep.

**What I can do.** Range, capital, threshold, strike range, full-range button, run, sweep, reopen runs.

**Data.** Coverage NIFTY+SENSEX 2023-10-03→2026-10-01, 738 days. Runs: #2 COMPLETED 60 trades net **−8021** (INDEX_FUTURES_PROXY, 15W/45L); #3 COMPLETED **0 trades** (Balanced gates). Dashboard still shows hist **−9971**.

**Settings.** Posted `strategy: {}` plus paper index config. Sweep uses `index_exits` variants. Option `choose_exit` still exists on the CE/PE path.

**Outputs.** `historical_paper_*` only. Alerts “historical paper alerts” read this book.

**Bugs.** H2 expiry heuristic inside the walk. H5 P&L ≠ Dashboard hist block. Run 3 zero trades looks “broken” but is gates, not an empty placeholder.

---

### 8 Option chain — PARTIAL

**Purpose.** Live ladder around ATM.

**What I can do.** Underlying, strikes above/below.

**Data.** Live Kite (stale after hours). 2026-10-03: spot 22421.95, **ATM 22400**, expiry **2026-10-06** (Tuesday — correct for current calendar), PCR 1.15, 7 rows. ATM 22400 CE OI 3,635,255 matches scanner.

**Settings.** None.

**Outputs.** None.

**Bugs.** M1 no paper banner. M3 ATM ≠ OI Scanner (22400 vs 22500). After-hours quotes look current.

---

### 9 OI Scanner — PARTIAL

**Purpose.** OI concentration and change.

**What I can do.** Expiry mode, window, period, sort, multi-strike control (local math, not saved).

**Data.** Same OI integers as chain on overlapping strikes. `state=MARKET_CLOSED`, interval change blank, `context.expiry` empty, **ATM 22500** (futures 22520). Session start 09:15.

**Settings.** `GET/PUT /api/oi-scanner/config` unused by UI.

**Bugs.** M3 ATM mismatch (`oi_scanner.py:201` futures-or-spot). M1 no banner.

---

### 10 Strike selection — PARTIAL

**Purpose.** One scored option or an explicit refusal.

**Data.** 2026-10-03: `available=false`, signal NO TRADE, direction NONE, spot 22421.95, ATR 10.36 (**matches Dashboard ATR**), 0 candidates, no ATM/expiry.

**Settings.** `strike_selection_config` + Strategy direction/signal. Not Option selection.

**Outputs.** Paper-trade → `position` (Positions screen).

---

### 11 Strike selection config — WORKING

**Purpose.** Per-underlying filters/weights for screen 10.

**Evidence.** `GET /api/strike-selection/config` 200, keys NIFTY, SENSEX. Save → PUT.

---

### 12 Strategy — WORKING

**Purpose.** Versioned 9-condition + CE/PE option-buy params.

**Evidence.** `GET /api/strategy` version **9**. Consumed by Dashboard/`market_view`. Not consumed by Alerts or index group scorer.

---

### 13 Option selection — PARTIAL

**Purpose.** ATM/offset pick for the original engine / Dashboard preferred contract.

**Data.** Real fields on `option_selection_rule`. Generic unlabelled dump via `ConfigScreen`.

**Used by.** `engine.py:768–780` `select_contract`. **Not** Strike selection.

---

### 14 Exits — PARTIAL

**Purpose.** Option/original-engine exit policy.

**Used by.** `choose_exit` in `paper_engine` OPTIONS path, `engine.py`, `option_buy.py`, `domain/backtest.py`. **Not** index proxy (`evaluate_index_exit`).

---

### 15 Risk — PARTIAL

**Purpose.** Engine + live-order risk + per-underlying ATR threshold.

**Used by.** Dashboard Risk block; `evaluate_risk` in engine and `chart_trading.py:571`. Paper Trading uses `paper_auto_setting` limits instead.

---

### 16 Broker — WORKING

**Purpose.** Kite session and arming.

**Evidence.** `GET /api/broker/status` 200: connected, PAPER, kill off, db up. Connect opens a new tab (`App.tsx:521`). Redis down does not 500 this endpoint.

**Bugs.** M4 can arm live from here despite paper-only product rule. No hypo banner.

---

### 17 Positions — PLACEHOLDER (for the paper books you actually use)

**Purpose.** Intended blotter; in practice an empty viewer of a third ledger.

**Evidence.** `GET /api/positions` `[]`, `GET /api/orders` `[]`. No banner. Does not read `paper_trade` or `historical_paper_trade` or Kite `positions().net`.

---

## Functional verification

### Dates

| Probe | Dashboard / live | Chart historical | Notes |
|---|---|---|---|
| Latest / 2026-10-03 Sat | NOT READY, last LTP 22421.95 | Live 0 candles; hist clamped to **2026-10-01** | User asked “no session, not fake data”. Hist **shows 1 Oct**. |
| 2024-06-04 election | n/a (live only) | Real crash session, 125 bars | Matches known big-move day. |
| Thu expiry before Sept 2025 (2025-08-28) | n/a | Real session | Code `is_index_expiry` → **False** (should be True per spec). |
| Tue expiry after Sept 2025 (2025-09-02) | n/a | Real session | Code → **True** (agrees with spec). |
| Holiday 2024-01-26 | n/a | Shows **2024-01-25** | Warning present; status still OK. |
| Weekend 2026-10-03 | NOT READY | Clamped | Live empty OK. |

Expiry function (`index_strategy.py:917–927`):

```
2024-08-29 Thu → NIFTY expiry True
2024-09-03 Tue → NIFTY expiry True   # spec: still Thursday until Sept 2025
2025-08-28 Thu → NIFTY expiry False  # spec: still Thursday
2025-09-02 Tue → NIFTY expiry True
2026-10-06 Tue → NIFTY expiry True   # live chain expiry is this date
SENSEX → Friday only
```

### Cross-screen numbers (same timestamp, 2026-10-03 after hours)

| Metric | Dashboard | Chart hist 2026-10-01 | Alerts | Paper live_decisions | Strike | Chain | OI Scanner |
|---|---|---|---|---|---|---|---|
| NIFTY LTP / close | 22421.95 spot / 22520 fut | last 22421.95 | score close **22520** | **22520** | 22421.95 | 22421.95 / ATM **22400** | ATM **22500** |
| EMA | 22521.85 (1m live) | 22402.72 (3m archive day) | EMA21 22521.85 | 22506.06 (3m live) | — | — | — |
| RSI | **49.24** | **60.07** | (not on snapshot) | **56.86** | — | — | — |
| ATR | **10.36** | **19.66** | — | **23.67** | **10.36** | — | — |
| CE / PE score | 33.33 / 22.22 | — | **24.67 / 43.33** | n/a (index) | NO TRADE | — | — |
| Decision | NOT READY / no pref | 0 live candles | WAIT | **BUY** | NO TRADE | — | MARKET_CLOSED |
| 22400 CE OI | — | — | — | — | — | 3635255 | 3635255 |

P&L books:

| Book | Trades | Net |
|---|---|---|
| Paper Trading today | 0 | 0 |
| Dashboard “Live paper P&L” | 18 | −751.28 |
| Historical run 2 API | 60 | −8021 |
| Dashboard “Latest historical paper P&L” | 60 | **−9971** |
| Positions | 0 | — |

### Indicator hand-check (2024-06-04, 125 3-minute archive bars)

Independent Wilder/EMA using the same formulas as `indicators.py`:

| | Hand (day bars only) | API last point | |Δ| |
|---|---|---|---|
| EMA20 | 21932.476 | 21932.479 | 0.003 |
| RSI14 | 54.580 | 54.582 | 0.002 |
| ATR14 | 85.381 | 85.366 | 0.015 |

Formula match. Residual is lookback/seed (API series may include a touch of prior context before filter). **Dashboard/Paper RSI/ATR for “now” are a different candle set** (live 1m vs live 3m vs archive 3m) — they will not match Chart historical.

Session: 125 × 3m from 09:15 = last bar open 15:27, covering to 15:30. No UTC on candle `ist` labels. Unix `time` is epoch (UTC); frontend shifts axis by +5:30 (`MarketChart.tsx:22–24`).

### Console / empty / no-op

- Frontend `:5999` serves HexTrade HTML (578 bytes index).
- Redis down: health `redis=down`; Dashboard still 200.
- Live chart Saturday: empty plot, status OK, no “no session” copy.
- Chain empty-state copy only if `rows` empty (not the case).
- Strike selection after hours: honest NO TRADE.
- Paper inputs that **do** persist: settings PUT. Mode buttons write `paper_mode`.
- `GET /api/oi-scanner/config` never wired — changing scanner defaults in DB does nothing in UI.
- Option selection / Exits / Risk Save works (new version) but several consumers ignore those rows (see overlap).

---

## Bugs (do not fix in this pass)

### H1 — Live Zerodha orders on “paper/alerts only” screens — High

Dashboard `LiveZerodhaPanel` and Chart `ChartStrikeTrade` call `POST /api/trading/zerodha/order`. Banner says no broker orders. Default is paper, but the control is on the main briefing/chart.

**Repro.** Broker connected → Dashboard or Chart → enable live with `PLACE REAL ORDERS` → BUY CE/PE. Order goes to Kite, not `paper_trade`.

### H2 — NIFTY weekly expiry switch is 2024-09-01, not Sept 2025 — High

```917:927:backend/app/domain/index_strategy.py
def is_index_expiry(symbol: str, day) -> bool:
    ...
    if isinstance(day, date_cls) and day >= date_cls(2024, 9, 1):
        return weekday == 1  # Tuesday
    return weekday == 3  # Thursday
```

**Repro.** Any index walk on 2025-08-28 (Thursday before Sept 2025): code says not expiry (square-off / buyer window wrong). 2024-09-03 Tuesday: code says expiry (too early). Calendar screen does **not** use this function (it uses listed expiries). Live chain 2026-10-06 Tuesday is fine.

### H3 — Four decision engines disagree; paper shows BUY on a Saturday — High

Same afternoon 2026-10-03:

- Dashboard: NOT READY, CE 33.33 / PE 22.22, RSI 49.24
- Alerts: WAIT, CE 24.67 / PE 43.33
- Paper Trading: NIFTY **BUY 75** (legacy 7 conditions), RSI 56.86
- Chart LIVE: 0 candles

`decide()` in `index_strategy.py:630` does not require session/weekday. `validate_index_entry` exists but is not applied to `live_index_decisions` (`paper_engine.py:890–925`).

### H4 — Holiday/weekend Chart does not show “no session” — High

`chart_service.py:148–163` snaps to the previous stored session and keeps `data_status=OK`. Saturday beyond archive end is **clamped** to 2026-10-01. User asked for empty / “no session”, not another day’s candles.

**Repro.** Chart → HISTORICAL ANALYSIS → 2024-01-26 or 2026-10-03 → Apply. You get 25 Jan / 1 Oct.

### H5 — P&L books do not match — High

Three (plus Zerodha) ledgers. Dashboard hist −9971 ≠ run 2 −8021. Paper today 0 ≠ dashboard live paper −751.28. Positions [].

**Repro.** Open Dashboard archive block, Paper Trading, Backdated Paper run 2, Positions. Four different answers.

### M1 — Required paper banner missing on several screens — Medium

Chain, OI Scanner, Strike selection, Strategy, Option selection, Exits, Risk, Broker, Positions.

### M2 — UTC leakage in ticker `as_of` and alert score `time` — Medium

UI ticker converts via `formatAsOfIst`. Alert table may show 12:34 when IST is 18:04.

### M3 — Option chain ATM ≠ OI Scanner ATM — Medium

Chain uses spot 22421.95 → 22400. Scanner uses `futures_ltp or ltp` (`oi_scanner.py:201`) 22520 → 22500. OI **integers** match.

### M4 — Redis down, live loop still “running” — Medium

`/api/health` redis=down. Live path `evaluate_risk` blocks LIVE if Redis is down; paper path does not need it. Confusing ops signal.

### L1 — `ExitAlerts` dead component — Low

`App.tsx:351`.

### L2 — Unused REST surface — Low

See dead-code table. Old `/api/backtests` can confuse a future sweep with `/api/historical-paper`.

### L3 — Live chart `data_status=OK` with 0 candles — Low

Saturday LIVE_PAPER.

---

## Status roll-up (17 specified screens)

| Status | Count | Screens |
|---|---|---|
| WORKING | 5 | Calendar patterns, Historical Data, Strike selection config, Strategy, Broker |
| PARTIAL | 11 | Dashboard, Chart, Alerts, Paper Trading, Backdated Paper, Option chain, OI Scanner, Strike selection, Option selection, Exits, Risk |
| BROKEN | 0 | — |
| PLACEHOLDER | 1 | Positions |

Extra Audit: WORKING as a log viewer (not counted).
