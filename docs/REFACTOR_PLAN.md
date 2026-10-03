# HexTrade refactor plan (not executed)

Read-only proposal. Do not merge, delete, or retarget screens until you approve a step.

---

## Target sidebar (grouped)

```
Monitor
  Dashboard          (briefing + risk strip + alert peek)
  Chart              (candles + markers; live-order rail hidden unless armed)
  Alerts             (CE/PE watches only)

Trade
  Paper              (tabs: Today | Backdated | Positions)
  Option chain       (tabs: Ladder | OI Scanner)

Research
  Calendar patterns
  Historical data

Settings
  Strategy pack      (tabs: Conditions | Option pick | Exits | Risk)
  Strike engine      (tabs: Run | Config)   — or fold Option pick in here later
  Broker
```

Audit becomes a footer link or a Settings sub-item, not a peer of Dashboard.

**Why group instead of deleting screens:** every current route has a real user question. The pain is 17 peers with overlapping names, not a lack of features.

---

## Overlap decisions

### Strike selection / Strike selection config / Option selection

**Recommendation: MOVE INTO TABS, then later MERGE pickers.**

| Screen | Job |
|---|---|
| Strike selection | Score listed options with Greeks, hard filters, explicit NO TRADE |
| Strike selection config | The knobs for that scorer (already the right split of run vs config) |
| Option selection | Older ATM/offset picker for Dashboard preferred + original engine |

These are **not** the same algorithm. Merging the UIs first (tabs: “Simple ATM pick” vs “Scored strike”) avoids two sidebar nouns that both mean “which contract”. Do **not** delete `option_selection_rule` until Dashboard preferred_contract is switched onto `strike_selection` (or vice versa) in one testable step.

**Keep config as a tab of the run screen** (same pattern as a single “Strike engine” item).

### Paper Trading / Backdated Paper / Positions

**Recommendation: MOVE INTO TABS under one Paper item. Do not merge the tables yet.**

| Book | Table | When |
|---|---|---|
| Today paper | `paper_trade` | Live session, auto/manual |
| Backdated | `historical_paper_*` | Archive walk / sweep |
| Positions | `position` | Dashboard ladder + strike paper + any LIVE rows |

They **must stay separate ledgers** until you add a single `trade` table with `source=LIVE_PAPER|HISTORICAL|ENGINE|BROKER`. Today they disagree on purpose (different writers) but the UI pretends they are one P&L.

Positions is a **PLACEHOLDER** relative to the books you actually use. First tab-unify the navigation; then either (a) make Positions a filter on `paper_trade` + Kite net, or (b) stop writing new fills to `position`.

### Strategy / Exits / Risk

**Recommendation: MOVE INTO TABS of one “Strategy pack” (already one `strategy_version`).**

They already save as one versioned row family (`GET /api/strategy` returns all three). Three sidebar items are the same form generator with different keys.

**Keep the data separate** (parameters vs exit_policy vs risk_policy). Document on the Exits tab: “Index paper uses `index_exits`, not these fields.” Do not silently point Exits at `evaluate_index_exit` without a migration — the field names do not map 1:1.

Index strategy JSON on Paper Trading stays a fourth pack until you promote it into the same version or accept two products: “option 9-condition” vs “index groups.”

### Option chain / OI Scanner

**Recommendation: MOVE INTO TABS. KEEP SEPARATE queries.**

OI integers already match. ATM does not (spot vs futures). One page, two tabs, **one ATM helper** (`futures` vs `spot` toggle, default spot to match the chain). Multi-Strike OI Control stays a panel on the OI tab.

### Dashboard / Alerts

**Recommendation: KEEP SEPARATE. Share the tape, not the decision.**

Dashboard answers “is the session usable and what is the preferred watch.” Alerts answers “did a CE/PE BUY confirm for notification.” Logic is **duplicated, not shared**:

- Dashboard: `evaluate_snapshot` + `option_buy` + `dashboard_brief`
- Alerts: `alert_engine.score_side` (EMA9/21, Supertrend, own RSI bands)

Do **not** merge the screens. Extract a shared `MarketFacts` (close, EMA20, VWAP, RSI, ATR, volume, OI) and let each scorer consume it. Until then, never expect the same CE/PE score on both.

---

## Logic implemented twice (canonical copy)

| Concern | Copies | Canonical to keep | Screens on the other copy |
|---|---|---|---|
| RSI / EMA / ATR / VWAP | `indicators.py` (one impl) used with different bars | Keep `indicators.py` | Alerts adds EMA9/21 via `alert_indicators.ema_pair` (OK wrapper) |
| Direction / tape | `implied_market_direction` vs configured `strategy.direction` vs alert trend | Keep `implied_market_direction` for “what the tape is” | Dashboard still also shows configured bias — label it |
| Option pick | `option_selection_rule` vs `strike_selection` | Decide later; today **both** | Dashboard vs Strike screen |
| Index score | New groups in `index_strategy.py` vs **stored legacy 7-weight JSON** still driving live_decisions | New `score_side` + gates | Paper UI text already describes the new groups; live payload still looks legacy |
| Exits | `choose_exit` vs `evaluate_index_exit` | Both, by instrument kind | Exits screen only edits the first |
| Risk limits | `risk_policy` vs `paper_auto_setting` | Paper book → paper settings; live orders → risk_policy | Dashboard Risk block mixes them |
| Backtest API | `/api/backtests` vs `/api/historical-paper` | historical-paper | unused old API |
| Expiry | listed `instrument.expiry` vs `is_index_expiry` | Listed expiries when present; heuristic only as fallback **after** fixing the Sept 2025 date | Index walk |

---

## Dead code to remove (only after the listed confirmation)

Confirm unused by `frontend/src` grep + no other backend caller you care about. Remove in the step order below, not as one dump.

| Item | Confirm unused | Risk if removed |
|---|---|---|
| `ExitAlerts` (`App.tsx:351`) | Not referenced in JSX | Low |
| Extra `/audit` nav (keep route) | Spec did not include it | Low |
| `GET /api/indicators` | No UI | Low |
| `GET /api/signals`, `/signals/current` | No UI; `engine.py` still **writes** `signal` | Medium — stop writes first or keep table |
| `/api/backtests*` + `backtest_run` / `backtest_trade` | No UI; tests may still import `domain/backtest.py` | Medium — keep domain tests, drop HTTP |
| `GET/PUT /api/oi-scanner/config` | UI never calls | Low if you accept in-memory OI Control |
| `GET/PUT /api/settings/broker` | Broker uses `/api/broker` | Low |
| `GET /api/underlyings/{symbol}/expiries` | No UI | Low |
| `GET /api/alerts/settings` | GET unused; PUT used | Low — delete GET only |
| `GET /api/strike-selection/runs` | No UI | Low |

Do **not** delete `index_strategy` “legacy close-to-close / implied tape weight 0” display rows until Paper live_decisions is proven on the new groups.

---

## Ordered, independently testable steps

### Step 1 — Freeze the product rule on the UI (banner + live rail)

**Risk:** Low  
**Do:** Add the paper banner to Chain, OI, Strike, Strategy pack, Positions. Move live-enable + Kite order rail behind Broker-only (or a single “Live” route). Do not change order APIs.  
**Verify:** Grep the banner string on every route. With `chart_live_enabled=false`, Chart/Dashboard have no BUY that calls `/api/trading/zerodha/order`. Broker still connects.

### Step 2 — Fix `is_index_expiry` to Sept 2025

**Risk:** Medium (changes historical square-off and expiry windows on walks)  
**Do:** Switch date to `2025-09-01`. Keep SENSEX Friday. Prefer listed expiry when `instrument.expiry` exists.  
**Verify:** Unit table: 2025-08-28 Thu True, 2024-09-03 Tue False, 2025-09-02 Tue True. Re-run a **short** historical range (one week around each) and compare trade counts to the previous run 2 snapshot (expect difference only on those weeks).

### Step 3 — Holiday / weekend Chart: empty, not snapped

**Risk:** Low  
**Do:** If `start==end` and no session, return `data_status=NO_SESSION`, `candles=[]`, do not rewrite `date_from`. Stop clamping a Saturday beyond archive end to the last weekday.  
**Verify:** 2024-01-26, 2026-10-03, 2024-06-08 (Sat) → 0 candles + explicit copy. 2024-06-04 still 125 bars.

### Step 4 — Session gate on live index `decide` display

**Risk:** Low  
**Do:** `live_index_decisions` must HOLD outside 09:30–14:45 IST, weekends, and holidays. Reuse `validate_index_entry` / session gate.  
**Verify:** Saturday API: NIFTY decision HOLD (today it was BUY 75). Weekday 10:30 with same bars can still BUY.

### Step 5 — One ATM helper for chain + OI

**Risk:** Low  
**Do:** Shared `atm_strike(spot, futures, interval, basis=spot|futures)`. Default spot. Show both on the combined Chain/OI page.  
**Verify:** Same timestamp, both tabs print the same ATM when basis is equal. OI integers unchanged.

### Step 6 — Tab-unify Paper (nav only)

**Risk:** Low  
**Do:** One route `/paper` with Today | Backdated | Positions. No table migration. Label each tab with its table name and “not the same P&L”.  
**Verify:** Deep links `/paper-trading`, `/backdated-paper`, `/positions` still work (redirect). Each tab’s API payload unchanged.

### Step 7 — Tab-unify Strategy pack (nav only)

**Risk:** Low  
**Do:** `/strategy` tabs Conditions | Option pick | Exits | Risk. Same PUT endpoints.  
**Verify:** Save on each tab still increments `version_number`. Dashboard still reads ATR from Risk.

### Step 8 — Tab-unify Strike engine (nav only)

**Risk:** Low  
**Do:** `/strike-selection` tabs Run | Config.  
**Verify:** Config save still per-underlying; Run still NO TRADE after hours.

### Step 9 — Tab-unify Chain | OI

**Risk:** Low  
**Do:** After step 5.  
**Verify:** OI Control still totals CE/PE in lakhs; chain PCR/max pain unchanged.

### Step 10 — Dashboard hist P&L = latest completed run

**Risk:** Low  
**Do:** `historical_paper_performance` must equal `GET /api/historical-paper/runs/{latest}` net (today −8021 vs −9971).  
**Verify:** Dashboard archive block matches Backdated run 2.

### Step 11 — Shared `MarketFacts` snapshot

**Risk:** Medium  
**Do:** One builder from bars (timeframe explicit). Dashboard, Alerts, Paper, Chart live all read it. **Do not** change scoring formulas in this step.  
**Verify:** Same timeframe + same bar: EMA20/RSI/ATR printed on Dashboard detailed snapshot **equal** Chart last point **equal** Paper indicators. Alerts may still use EMA9/21 but should show the shared EMA20 alongside.

### Step 12 — Retire unused HTTP

**Risk:** Low  
**Do:** Dead-code table. Keep writing `audit_event`.  
**Verify:** Frontend build; `pytest` domain suites; no 404 from remaining UI.

### Step 13 — Single trade ledger (optional, last)

**Risk:** High  
**Do:** Only after tabs exist. Add `source` and keep old tables as views for one release.  
**Verify:** One trade opened from Paper Today appears on Positions tab; a Backdated fill does **not**. Kite net stays labelled LIVE.

---

## What not to do in the first pass

- Do not invent a fifth scorer.
- Do not “fix” historical run 2 by re-walking the full 2023–2026 archive unless you ask for it.
- Do not point Exits UI at `index_exits` without mapping fields.
- Do not delete `option_selection_rule` or `position` until writers are moved.
- Do not enable live trading as part of cleanup.

---

## Suggested first approval slice

Steps **1, 3, 4, 10** (banner/live rail, no-session chart, weekend HOLD, P&L label fix). No nav regroup yet. Each is independently testable and does not require a data migration.
