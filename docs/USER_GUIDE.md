# HexTrade user guide

This is a **paper-trading and alert** app for **NIFTY** and **SENSEX** CE/PE options. It does not place broker orders unless you deliberately arm live trading on **Broker** (type `PLACE REAL ORDERS`). Chart and Dashboard can only *use* that latch; they cannot turn it on.

All session times are **Asia/Kolkata (IST)**. Regular cash session is **09:15–15:30**. Stored index history covers **2023-10-03 → 2026-10-01** (738 sessions each for NIFTY and SENSEX). When option history is missing, the app uses the **index close as a futures proxy**. That is not option-premium P&L.

NIFTY weekly expiry is **Thursday before 1 Sept 2025**, **Tuesday from 1 Sept 2025**. SENSEX weekly expiry is Friday.

Every screen shows this banner:

> HYPOTHETICAL / PAPER signals only. No broker orders

**Market Ready is not a BUY.** A score is not a forecast.

## Where each old screen lives now

| Old sidebar item | New location |
|---|---|
| Dashboard | Monitor → Dashboard |
| Chart | Monitor → Chart |
| Alerts | Monitor → Alerts |
| Paper Trading | Trade → Paper → Today |
| Backdated Paper | Trade → Paper → Backdated |
| Positions | Trade → Paper → Positions |
| Option chain | Trade → Option chain → Ladder |
| OI Scanner | Trade → Option chain → OI Scanner |
| Calendar patterns | Research → Calendar patterns |
| Historical Data | Research → Historical Data |
| Strategy | Settings → Strategy → Conditions |
| Option selection | Settings → Strategy → Option pick |
| Exits | Settings → Strategy → Exits |
| Risk | Settings → Strategy → Risk |
| Strike selection | Settings → Strike engine → Run |
| Strike selection config | Settings → Strike engine → Config |
| Broker | Settings → Broker |
| Audit | Settings → Audit (route `/audit` unchanged) |

Old URLs still work and redirect: `/paper-trading`, `/backdated-paper`, `/positions`, `/oi-scanner`, `/selection`, `/exits`, `/risk`, `/strike-selection/config`.

---

## Typical daily workflow

**Before the open (or any morning you review)**

1. **Historical Data** — confirm archive still covers 2023-10-03 → last session (738 days). Do not re-import unless quality looks incomplete.
2. **Broker** — confirm Zerodha is connected if you want a live option chain / OI. Keep mode **Paper**. Do not type `PLACE REAL ORDERS`.
3. **Calendar patterns** — optional. Look at weekday / expiry-proximity averages. These are history, not a plan for today.

**During the live session (09:15–15:30 IST)**

4. **Dashboard** — one-page briefing: is the tape usable, which way is it leaning, which CE/PE is preferred, paper P&L, risk room.
5. **Chart** — same instrument, 3-minute candles, EMA/VWAP. Confirm the dashboard story on the bars. Stay on **LIVE PAPER**. A weekend or holiday shows **no session**, not another day’s candles.
6. **Alerts** — CE/PE BUY watches only. Acknowledge or dismiss. Alerts never send an order. Shared EMA20 is shown next to this screen’s own EMA9/21.
7. **Option chain** — Ladder and OI Scanner tabs. Same ATM (spot) on both. An OI increase is not proof of long buildup.
8. **Strike engine** — Run a scored contract (delta, spread, R:R); Config is the other tab. Paper-trade from here writes a **different** book than Paper → Today.
9. **Paper** — Today tab for hypothetical index or option trades. Auto-paper default is OFF. Backdated and Positions are the other two tabs (different tables).

**After the close / weekend review**

10. **Chart** in **HISTORICAL ANALYSIS** — pick a date (election day, expiry, your last trade). Replay if you want the markers to walk forward.
11. **Paper → Backdated** — run a range or a threshold/exit sweep. Read the INDEX PROXY warning. This book is not the same as today’s Paper book.
12. **Paper → Positions** — only the old `position` table (dashboard paper-orders and strike-selection paper). It will usually **not** match Today or Backdated.
13. **Strategy pack / Strike engine Config** — change rules here, then re-check Dashboard and a short backtest. Do not expect every screen to pick up every setting (see each screen below). Dashboard “Latest historical paper P&L” is the latest completed Backdated run’s net.

---

## 1. Dashboard

**Purpose.** “Is the market usable right now, which way is the tape leaning, and which CE/PE is the preferred watch — without treating that as a BUY?”

**How to use.** Open it at the start of the session. Read Market Ready first. If it is NOT READY (weekend, holiday, or outside hours), stop — do not hunt for a trade. Expand “Option ladder” only when you want to record a **paper** buy. Expand “Archive, paper P&L, and data quality” for coverage and hypothetical P&L.

**What the numbers mean.**

- **Market Ready / WAITING / NOT READY** — session and data quality only. Never a BUY.
- **Trend (BULLISH / BEARISH / NEUTRAL)** — close vs EMA, VWAP, and momentum votes. Not the Strategy screen’s configured bias.
- **Green indicators x / 9** — how many of the Strategy 9-condition checks are true on the last live candle.
- **CE score / PE score** — independent option-buy match (same family of 9 checks on premium + futures). Percent of checks passed, not probability of profit.
- **Preferred contract** — one CE or PE from the live chain using **Option selection** rules (ATM / offset). Empty when the tape is not confirmed.
- **Risk** — mode, daily P&L room, open exposure from the `position` table.
- **Live paper P&L / historical paper P&L** — two different books. Live paper is `paper_trade`. Historical paper is the latest completed Backdated run (`historical_paper_*`). Neither is Positions.

---

## 2. Calendar patterns

**Purpose.** “On stored index sessions, did certain weekdays, weeks of the month, or expiry-adjacent days tend to close up or down?”

**How to use.** Leave dates empty for the full archive, or narrow a month. Filters (weekday, month, expiry proximity, regime) re-query after a short pause. Click NIFTY / SENSEX when both are loaded. Click a heatmap cell to jump to Chart with those dates highlighted.

**What the numbers mean.** Open-to-close **percent on the index close** (futures proxy). A positive average is not a prediction. Groups with fewer than the minimum sample are weak. Expiry proximity uses listed expiries in the instrument master — it does **not** invent Thursday/Tuesday from the weekday.

---

## 3. Chart

**Purpose.** “What did the index actually do on this session, with EMA/VWAP/RSI/ATR, and where would paper BUY/SELL markers have fired?”

**How to use.**

- Instrument: NIFTY or SENSEX.
- Timeframe: 3m (default; what paper index uses), 5m, 15m, 1m.
- Mode: **LIVE PAPER** (today’s live candles), **HISTORICAL ANALYSIS** (stored archive), **HISTORICAL REPLAY** (walk bars forward).
- Historical: pick start/end or a month, then **Apply**. Quick ranges: today / yesterday / 7d / 30d.
- Toggles: EMA20, VWAP, volume, RSI, ATR, OI; BUY/SELL/CE/PE marker filters; entry/target/stop/exit lines.
- Replay: play / speed / cursor.

**What the numbers mean.** Candles are IST. The last 3-minute bar of a session is labelled **15:27** (that bar covers 15:27–15:30). Markers are hypothetical. If option history is missing you will see index BUY/SELL (long/short the index), not CE/PE premium.

**Caution.** The strike rail can send a **real Zerodha order** only if Broker has armed chart live trading. The enable control is not on Chart. Weekend/holiday single-day views return **NO_SESSION** and an empty plot.

---

## 4. Alerts

**Purpose.** “Did a CE or PE BUY setup confirm, and should I look at it — without placing anything?”

**How to use.** Toggle which underlyings and sides to watch. Set minimum score, CE vs PE gap, cooldown, spread/volume/OI floors, stop % and R:R targets. Acknowledge or dismiss an open alert. Historical paper alerts are a replay log, not live.

**What the numbers mean.** Score is this screen’s own CE/PE scorer (trend, EMA 9/21, Supertrend, VWAP, RSI band, liquidity). It is **not** the Dashboard 9-condition percent and **not** the Paper index score. Shared last-bar EMA20 / RSI / ATR come from the same `MarketFacts` helper as Dashboard and Chart. WAIT means hours, score, liquidity, or trend did not confirm.

---

## 5. Paper (Today | Backdated | Positions)

Today is the old Paper Trading screen. Backdated and Positions are the other tabs of the same item. The three tables are still separate ledgers.

### Today tab

Old route `/paper-trading` redirects here.

**Purpose.** “If I take a hypothetical index or option trade with my paper rules, what is today’s P&L?”

**How to use.**

- Mode: Manual / Automatic / Historical backtest / Historical replay.
- Manual: BUY/SELL the index future quote (not live broker).
- Automatic: enable Auto Paper Trade (default OFF). Still no broker order.
- Index strategy panel: BUY/SELL thresholds, presets (Conservative / Balanced / Aggressive), gates.
- Settings: lots, max trades/day, max daily loss, square-off time, cooldown, CE/PE vs INDEX instrument.
- Click a row for trade detail.

**What the numbers mean.** Gross is price move × qty. Net subtracts estimated charges. Index paper is **index points**, not option premium. Today’s table is the `paper_trade` book only.

---

## 6. Historical Data

**Purpose.** “Is the 1-minute NIFTY/SENSEX archive complete enough to trust charts and backtests?”

**How to use.** Import Last 3 Years (INITIAL) or Import Missing Data (INCREMENTAL). Resume if a job failed. This writes `market_historical_data` only — it does not mix into the live candle feed and never sends an order.

**What the numbers mean.** Trading days, imported vs expected candles, missing/duplicate/invalid. Timezone Asia/Kolkata. Option history is **not** stored (Kite does not give expired contracts). Backtests will say so and use index close as proxy.

---

### Backdated tab

Old route `/backdated-paper` redirects here.

## 7. Backdated Paper (same as Paper → Backdated)

**Purpose.** “On stored history, with this threshold and exit style, what would hypothetical index (or option) paper have made?”

**How to use.** Pick NIFTY / SENSEX / BOTH, date range inside the archive, starting capital, threshold %, optional strike range. **Run paper on this range**, or **Run 65–85 × exit sweep** (train 2023-10-03→2025-08-31, test 2025-09-01→2026-10-01). Click a previous run to reopen it.

**What the numbers mean.** NET P&L is hypothetical. INDEX PAPER means BUY = long index, SELL = short index at the close. Charges on proxy trades should be index-style, not NFO option rates. Run results live in `historical_paper_*` tables — not in Paper Trading and not in Positions.

---

## 8. Option chain (Ladder | OI Scanner)

**Purpose.** “What is the live (or last) option ladder around ATM — LTP, OI, volume, IV, PCR, max pain?”

**How to use.** Choose NIFTY or SENSEX. Widen/narrow strikes above/below ATM. Needs a connected broker and a refreshed instrument master. Switch to the **OI Scanner** tab for interval OI change and Multi-Strike OI Control.

**What the numbers mean.** Spot, ATM (from **spot**), nearest expiry, PCR, max pain, CE/PE OI totals. OI increase is evidence of positioning, **not** proof of long buildup. After hours you still see the last quotes (they can look “live” while the market is closed). Ladder ATM and OI Scanner ATM match when both use the same spot basis.

---

## 9. OI Scanner (Option chain → OI Scanner tab)

Old route `/oi-scanner` redirects here.

**Purpose.** “Where is open interest concentrated, and how did it change over 5/10/15 minutes or since 10:30 / open?”

**How to use.** Underlying, expiry mode (nearest / next / all / specific), strike window, change period. Sort columns. Multi-Strike OI Control lets you pick CE and PE strikes and total OI in lakhs.

**What the numbers mean.** Raw OI and interval change. After hours, interval change is often blank (MARKET_CLOSED). ATM uses the same **spot** helper as the ladder (default). OI integers are unchanged.

---

## 10. Strike engine (Run | Config)

### Run tab

Old route `/strike-selection`.

**Purpose.** “Given the current signal, which single listed option best passes my hard filters and score?”

**How to use.** Pick NIFTY or SENSEX. Read the signal card. If a contract is selected, tick acknowledge and paper-trade. If you see NO SUITABLE OPTION, the engine refused to lower the bar — that is intended.

**What the numbers mean.** Score ranks suitability (liquidity, spread, delta, R:R, IV, ATM distance). Greeks are Black-76 estimates, not exchange-published. Paper-trade writes the **Positions** book (`position`), not Paper Trading’s `paper_trade` book.

---

### Config tab

Old route `/strike-selection/config` redirects here.

## 11. Strike selection config (same as Strike engine → Config)

**Purpose.** “What filters and weights should Strike selection use for NIFTY vs SENSEX?”

**How to use.** Switch underlying first (they are stored separately). Edit liquidity floors, premium band, delta bands, IV action, ATR multiple, stop/target, sizing, and score weights. Save.

**What the numbers mean.** Hard filters reject a strike even if the score is high. Weights are normalised to 0–100. Min score is a gate, not a win-rate.

---

## 12. Strategy pack (Conditions | Option pick | Exits | Risk)

Old routes `/selection`, `/exits`, `/risk` redirect to the matching tab. Saving on any tab still creates a new strategy version.

### Conditions tab

**Purpose.** “What are the 9 Dashboard conditions (EMA, momentum, OI, volume, VWAP, RSI, ATR, candle, time) and the CE/PE option-buy extras?”

**How to use.** Edit per underlying. Saving creates a new version. Confirmation mode “all” vs “min_conditions” applies to analysis; live auto-entry still wants every enabled condition.

**What the numbers mean.** These drive **Dashboard** and the original engine. They do **not** drive Alerts, Paper Trading index BUY/SELL groups, or Strike selection scores. ATR **period** is here; ATR **threshold** is on Risk (NIFTY and SENSEX are independent).

---

## 13. Option selection (Strategy → Option pick)

**Purpose.** “When Dashboard/engine wants a CE or PE, how do we pick the contract (ATM, offset, premium/volume/OI/spread floors, nearest expiry)?”

**How to use.** Generic field list. Save creates a new strategy version.

**What the numbers mean.** Used by the original signal → contract path. **Not** used by Strike selection (that has its own config). Changing this does not change the Strike selection winner.

---

## 14. Exits (Strategy → Exits)

**Purpose.** “For option paper / original engine positions, when do we stop, target, trail, or time-exit?”

**How to use.** Edit stop/target/trail styles, reversal flags, 15:15 time exit, priority string. Save.

**What the numbers mean.** Consumed by `choose_exit` for **option** paper and the old `position` book. **Index** paper and Backdated Paper index proxy use a **different** exit engine (structural stop, 1R partial, chandelier/EMA trail, opposite signal). Editing Exits does not change those index exits.

---

## 15. Risk (Strategy → Risk)

**Purpose.** “What capital, loss, trade-count, and session windows may the original engine and live-order path use?”

**How to use.** Set NIFTY and SENSEX ATR thresholds separately. Edit max capital, daily loss, trades/day, exposure, spread, entry window, square-off. Save.

**What the numbers mean.** Dashboard Risk block and live-order checks read this. Paper Trading has **its own** max daily loss / max trades. The two limits can disagree.

---

## 16. Broker

**Purpose.** “Is Kite connected, and is the app allowed to send real orders?”

**How to use.** Connect Zerodha (opens a new tab). Paste `request_token` if the redirect does not return here. Refresh instruments after login. Prefer **Paper trading** / **Analysis only**. Kill switch aborts live intent. This is the **only** screen that can arm live trading or enable Chart live orders. Type `PLACE REAL ORDERS` only if you understand it can debit the real account.

**What the numbers mean.** Status, user, masked API key, expiry, mode, kill switch, auto-entry armed, chart-live latch. Tokens stay on the backend.

---

## 17. Positions (Paper → Positions)

Old route `/positions` redirects here.

**Purpose.** “What is in the original `position` + `order_record` tables?”

**How to use.** View rows. Close an OPEN row. This is **not** “all my paper trades” and **not** “my live Zerodha book” (that is on Dashboard → Live Zerodha panel, view/sync only).

**What the numbers mean.** Mode PAPER vs LIVE, entry, LTP, P&L, stop, target. Empty usually means nothing was written through dashboard paper-orders or strike-selection paper-trade.

---

## Extra screen: Audit

Settings → Audit. Route `/audit` is unchanged. It is a raw `audit_event` log (signal rejected, mode changes, etc.). It does not trade.
