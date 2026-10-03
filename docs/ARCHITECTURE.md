# HexTrade architecture (as built)

Updated after `refactor/screens`. Saturday 2026-10-03 evening.

Stack: FastAPI + SQLAlchemy 2 + SQL Server (`localhost\SQLEXPRESS`) on `:8000`. React 18 + Vite on `:5999`. Redis optional (currently **down**). Timezone intended: Asia/Kolkata.

---

## Screens → services → stores

```mermaid
flowchart LR
  subgraph UI["Frontend hubs"]
    DASH[Dashboard]
    CAL[Calendar patterns]
    CHT[Chart]
    AL[Alerts]
    PAP[Paper Today Backdated Positions]
    HD[Historical Data]
    CHN[Chain Ladder plus OI]
    SS[Strike Run plus Config]
    ST[Strategy Conditions Option Exits Risk]
    BR[Broker]
    AUD[Audit]
  end

  subgraph API["FastAPI"]
    ENG[engine.dashboard / market_view]
    OBUY[option_buy]
    CSVC[chart_service]
    CTRD[chart_trading]
    AENG[alert_engine]
    PENG[paper_engine]
    HIMP[historical_import]
    HBT[historical_backtest]
    CHAIN[public_chain]
    OIS[oi_scanner]
    SEL[selection + strike_selection]
    MAP[strategy mapping]
    FACTS[market_facts]
    KITE[Kite adapter]
  end

  subgraph DB["SQL Server"]
    CANDLE[(candle live)]
    MHD[(market_historical_data)]
    STRAT[(strategy_version + params)]
    OSR[(option_selection_rule)]
    EXP[(exit_policy)]
    RSK[(risk_policy)]
    SSCFG[(strike_selection_config)]
    PT[(paper_trade)]
    HPT[(historical_paper_*)]
    POSTabl[(position + order_record)]
    ALERT[(alert_*)]
    CSIG[(chart_signal)]
    BROK[(broker_session)]
    SET[(app_setting)]
    INST[(instrument)]
    AUDT[(audit_event)]
  end

  DASH --> ENG
  DASH --> OBUY
  DASH --> CTRD
  ENG --> FACTS
  OBUY --> FACTS
  CSVC --> FACTS
  PENG --> FACTS
  AENG --> FACTS
  CAL --> HIMP
  CHT --> CSVC
  CHT --> CTRD
  AL --> AENG
  PAP --> PENG
  PAP --> HBT
  PAP --> ENG
  HD --> HIMP
  CHN --> CHAIN
  CHN --> OIS
  SS --> SEL
  ST --> MAP
  BR --> KITE
  AUD --> ENG

  ENG --> CANDLE
  ENG --> STRAT
  ENG --> POSTabl
  OBUY --> CANDLE
  OBUY --> STRAT
  CSVC --> MHD
  CSVC --> CANDLE
  CSVC --> CSIG
  CTRD --> BROK
  CTRD --> SET
  AENG --> ALERT
  PENG --> PT
  HIMP --> MHD
  HBT --> MHD
  HBT --> HPT
  CHAIN --> INST
  OIS --> INST
  SEL --> SSCFG
  SEL --> POSTabl
  MAP --> STRAT
  MAP --> OSR
  MAP --> EXP
  MAP --> RSK
  KITE --> BROK
  KITE --> INST
```

---

## Two candle stores (do not mix)

```mermaid
flowchart TB
  KITE[Kite historical API] -->|import job only| MHD[(market_historical_data)]
  KITE2[Kite websocket / polling] --> CANDLE[(candle)]
  MHD --> CHT[Chart HISTORICAL_*]
  MHD --> CAL[Calendar]
  MHD --> BD[Paper Backdated tab]
  CANDLE --> DASH[Dashboard / market_view]
  CANDLE --> LIVE[Chart LIVE_PAPER]
  CANDLE --> PAP[Paper live_decisions]
  CANDLE --> AL[Alerts tape]
```

Rule in code: historical replay must not read live candles. Live Dashboard/Paper/Alerts do not read the archive. That is why **RSI/EMA/ATR disagree** across Dashboard vs Chart historical vs Paper 3m on the same wall-clock time.

---

## Signal flow (intended vs actual)

Intended product story:

```mermaid
flowchart TD
  C[Candles IST 09:15-15:30] --> I[indicators.py]
  I --> F[market_facts last bar]
  F --> G[Gates + scores]
  G --> A[Alert]
  A --> P[Paper trade]
  P --> X[Exit]
  X --> PnL[P and L]
```

**Actual:** that pipeline is implemented **four times**, and the arrows do not all connect.

```mermaid
flowchart TD
  subgraph Live1m["Path A — Dashboard / original engine"]
    C1[live candle 1m] --> I1[market_facts + indicators.py]
    I1 --> S9[strategy.evaluate_snapshot 9 conditions]
    S9 --> OB[option_buy CE/PE 9 checks]
    OB --> PRES[dashboard_brief presentation]
    PRES --> UI1[Dashboard card]
    S9 --> PICK[option_selection_rule ATM/offset]
    PICK --> POS[(position)]
    POS --> X1[exits.choose_exit]
    X1 --> R1[risk.evaluate_risk]
  end

  subgraph Live3m["Path B — Paper index + Chart markers"]
    C2[live or archive 3m] --> I2[market_facts + features_from_bars]
    I2 --> IDX[index_strategy.score_side / decide]
    IDX --> UI2[Paper live_decisions / Chart markers]
    IDX --> PT[(paper_trade INDEX)]
    PT --> X2[index_exits.evaluate_index_exit]
  end

  subgraph AlertPath["Path C — Alerts"]
    C3[live candles] --> I3[market_facts EMA20 plus ema_pair 9/21 + Supertrend]
    I3 --> AS[alert_engine.score_side]
    AS --> AL[(alert_signal)]
  end

  subgraph StrikePath["Path D — Strike selection"]
    C4[live market_view + chain] --> SS[strike_selection.evaluate + Black76]
    SS --> POS2[(position)]
  end

  subgraph Hist["Path E — Backdated"]
    MHD[(market_historical_data)] --> HBT[historical_backtest walk]
    HBT --> IDX
    HBT --> HPT[(historical_paper_trade)]
    HPT --> UI3[Backdated Paper + Alerts historical table]
  end
```

Nothing in Path C writes a paper trade. Path B does not write Path A `position`. Path E does not update Path B’s today blotter. Live Kite orders (Chart/Dashboard) bypass all of the above and write `trading_order_audit` + the broker.

---

## Scoring copies (which screen uses which)

| Scorer | Function | Screen |
|---|---|---|
| 9 boolean conditions + configured direction | `strategy.evaluate_snapshot` | Dashboard tape votes, Strike “signal”, Chart live condition count |
| CE/PE 9-check percent | `option_buy.evaluate_*` | Dashboard CE/PE blocks |
| Implied trend 3 votes | `dashboard_brief.implied_market_direction` | Dashboard trend glyph; alert tape; paper tape |
| Index groups + mandatory gates | `index_strategy.score_side` / `decide` | Paper panel, Chart hist markers, Backdated walk |
| Alert CE/PE weighted score | `alert_engine.score_side` | Alerts only |
| Strike suitability 0–100 | `strike_selection` weights | Strike selection only |

RSI is one **function** (`indicators.rsi`, Wilder 14) via `market_facts_from_bars` for the last bar. Inputs still differ by timeframe (1m live vs 3m live vs 3m archive day). Alerts also show EMA9/21. Pin tests lock 2024-06-04 / 2025-08-28 / 2025-09-02 last EMA/RSI/ATR.

---

## Exit copies

```mermaid
flowchart LR
  OPT[Option paper / old position] --> CE[choose_exit + exit_policy]
  IDX[Index paper + hist proxy] --> IE[evaluate_index_exit]
  LIVE[Zerodha live] --> BROKER[Kite / user]
```

`exit_policy` (Exits screen) feeds `choose_exit` only. Index structural stop / partial 1R / chandelier lives in `index_exits.py` and is **not** editable on Exits.

---

## P&L ledgers

```mermaid
flowchart TB
  PAP[Paper Trading UI] --> PT[(paper_trade)]
  BD[Backdated Paper UI] --> HPT[(historical_paper_trade)]
  DASH[Dashboard ladder / Strike paper] --> POS[(position)]
  LIVE[LiveZerodhaPanel] --> KITE[Kite net positions]
  DASH --> SUM1[paper_performance on paper_trade window]
  DASH --> SUM2[historical_paper_performance]
```

These four numbers are not one ledger (Step 13 skipped). Verified after Step 10: today paper 0; dashboard live-paper −751.28 / 18 trades; hist API run 2 **−8021**; dashboard hist **−8021**; positions empty.

---

## Broker / live vs paper

```mermaid
flowchart TD
  BR[Broker screen] -->|OAuth| SESS[(broker_session encrypted)]
  SESS --> CHAIN[Chain / OI / quotes]
  SESS --> LIVE{chart_live_enabled AND phrase PLACE REAL ORDERS}
  LIVE -->|no| PAPER[paper_trade / position PAPER]
  LIVE -->|yes| ORDER[KiteOrderGateway]
  ORDER --> AUD[(trading_order_audit)]
```

`app_setting.trading_mode` and `chart_live_enabled` are separate latches. Only **Broker** can arm live or enable Chart live orders. Restart disarms live.

---

## Expiry

| Consumer | Source |
|---|---|
| Option chain / OI / strike | Listed `instrument.expiry` (live 2026-10-06 Tuesday) |
| Calendar proximity | Listed `instrument.expiry` years &gt; 1990 |
| Index paper / backtest square-off | `is_index_expiry()` weekday heuristic, switch **2025-09-01**; listed expiries win when passed |

SENSEX heuristic is Friday. NIFTY is Thursday until 1 Sept 2025, Tuesday after.

---

## Runtime topology (verified)

- API `:8000` — health `ok`, database `up`, redis `down`, trading_loop `running`
- UI `:5999` — Vite index contains HexTrade
- Session closed (Saturday)
- Broker connected, mode PAPER, kill switch off, live trading badge off
