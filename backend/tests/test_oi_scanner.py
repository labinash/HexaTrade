from datetime import date, datetime, time, timedelta

from app.domain.oi_scanner import (
    CLOSED,
    LIVE,
    PRE_OPEN,
    Contract,
    ScannerConfig,
    Snapshot,
    build_row,
    classify_price_oi,
    market_state,
    passes,
    safe_percent,
    summarise,
    top_movers,
    window_strikes,
)

EXPIRY = date(2026, 10, 8)
UTC_NOW = datetime(2026, 9, 29, 5, 5)


def contract(strike: float, option_type: str = "CE", token: int | None = None) -> Contract:
    return Contract(
        instrument_token=token or int(strike),
        tradingsymbol=f"NIFTY26O08{int(strike)}{option_type}",
        underlying="NIFTY",
        expiry=EXPIRY,
        strike=strike,
        option_type=option_type,
        lot_size=75,
    )


def snap(oi: int | None, ltp: float | None = 100.0, minutes_ago: int = 0) -> Snapshot:
    return Snapshot(oi=oi, ltp=ltp, volume=5000, at=UTC_NOW - timedelta(minutes=minutes_ago))


def config(**overrides) -> ScannerConfig:
    base = dict(min_oi_change_interval=0, min_oi_change_reference=0)
    base.update(overrides)
    return ScannerConfig(**base)


def row(live: Snapshot, previous: Snapshot, reference: Snapshot, cfg: ScannerConfig | None = None, state: str = LIVE):
    return build_row(contract(25000), live, previous, reference, cfg or config(), state, 25000.0, 50.0)


def test_market_state_distinguishes_before_open_from_after_close():
    start, end = time(9, 15), time(15, 30)
    assert market_state(time(9, 0), start, end) == PRE_OPEN
    assert market_state(time(10, 30), start, end) == LIVE
    assert market_state(time(15, 45), start, end) == CLOSED


def test_five_minute_change_is_current_minus_previous():
    built = row(snap(138000), snap(125000, minutes_ago=5), snap(125000, minutes_ago=65))
    assert built.oi_change_interval == 13000
    assert round(built.oi_change_interval_percent, 2) == 10.4


def test_reference_change_is_independent_of_the_interval_change():
    built = row(snap(540000), snap(538000, minutes_ago=5), snap(500000, minutes_ago=85))
    assert built.oi_change_interval == 2000
    assert built.oi_change_reference == 40000
    assert round(built.oi_change_reference_percent, 2) == 8.00


def test_percent_never_returns_infinity_or_nan():
    assert safe_percent(100, 0) is None
    assert safe_percent(100, None) is None
    assert safe_percent(None, 100) is None
    built = row(snap(5000), snap(0, minutes_ago=5), snap(0, minutes_ago=65))
    assert built.oi_change_interval == 5000
    assert built.oi_change_interval_percent is None
    assert "zero" in built.interval_note


def test_missing_interval_snapshot_explains_itself():
    built = row(snap(138000), Snapshot(), snap(125000, minutes_ago=65))
    assert built.oi_change_interval is None
    assert built.interval_note == "No 5-minute reference snapshot available"


def test_missing_reference_snapshot_names_the_reference_time():
    built = row(snap(138000), snap(125000, minutes_ago=5), Snapshot(), config(reference_time=time(10, 0)))
    assert built.oi_change_reference is None
    assert built.reference_note == "No 10:00 reference snapshot available"


def test_missing_live_data_says_instrument_data_unavailable():
    built = row(Snapshot(), snap(125000, minutes_ago=5), snap(120000, minutes_ago=65))
    assert built.interval_note == "Instrument data unavailable"
    assert built.reference_note == "Instrument data unavailable"


def test_before_open_does_not_compute_a_misleading_change():
    built = row(snap(138000), snap(125000, minutes_ago=5), snap(120000, minutes_ago=65), state=PRE_OPEN)
    assert built.oi_change_interval is None
    assert built.oi_change_reference is None
    assert built.interval_note == "Market not open"


def test_after_close_withholds_the_rolling_interval_change():
    built = row(snap(138000), snap(125000, minutes_ago=5), snap(120000, minutes_ago=65), state=CLOSED)
    assert built.oi_change_interval is None
    assert built.interval_note == "Market closed, 5-minute change not calculated"
    assert built.oi_change_reference == 18000, "a change since the reference time is still meaningful at close"
    assert any("Market closed" in note for note in built.notes)


def test_after_close_computes_the_interval_change_when_history_is_requested():
    built = build_row(
        contract(25000), snap(138000), snap(125000, minutes_ago=5), snap(120000, minutes_ago=65),
        config(), CLOSED, 25000.0, 50.0, historical=True,
    )
    assert built.oi_change_interval == 13000
    assert built.interval_note == ""


def test_price_and_oi_quadrants_are_possibilities_not_instructions():
    assert classify_price_oi(5, 1000) == ("LONG_BUILDUP", "Potential long buildup")
    assert classify_price_oi(-5, 1000) == ("SHORT_BUILDUP", "Potential short buildup")
    assert classify_price_oi(5, -1000) == ("SHORT_COVERING", "Potential short covering")
    assert classify_price_oi(-5, -1000) == ("LONG_UNWINDING", "Potential long unwinding")
    assert classify_price_oi(None, 1000)[0] == "UNKNOWN"
    assert classify_price_oi(5, None)[0] == "UNKNOWN"
    for _, label in [classify_price_oi(a, b) for a in (5, -5) for b in (1000, -1000)]:
        assert "buy" not in label.lower() and "sell" not in label.lower()


def test_oi_increase_alone_yields_no_interpretation():
    built = row(snap(138000, ltp=None), snap(125000, ltp=None, minutes_ago=5), Snapshot())
    assert built.oi_change_interval == 13000
    assert built.interpretation_code == "UNKNOWN"


def test_significance_uses_the_configured_threshold_both_ways():
    cfg = config(min_oi_change_interval=10000)
    up = row(snap(138000), snap(125000, minutes_ago=5), Snapshot(), cfg)
    down = row(snap(112000), snap(125000, minutes_ago=5), Snapshot(), cfg)
    small = row(snap(126000), snap(125000, minutes_ago=5), Snapshot(), cfg)
    assert up.significant_increase and not up.significant_decrease
    assert down.significant_decrease and not down.significant_increase
    assert not small.significant_increase and not small.significant_decrease


def test_direction_filters_select_the_right_rows():
    cfg_up = config(direction="INCREASE")
    up = row(snap(138000), snap(125000, minutes_ago=5), Snapshot(), cfg_up)
    down = row(snap(112000), snap(125000, minutes_ago=5), Snapshot(), cfg_up)
    assert passes(up, cfg_up)
    assert not passes(down, cfg_up)
    cfg_sig = config(direction="SIGNIFICANT_DECREASE", min_oi_change_interval=10000)
    big_down = row(snap(112000), snap(125000, minutes_ago=5), Snapshot(), cfg_sig)
    assert passes(big_down, cfg_sig)


def test_negative_threshold_keeps_only_reductions():
    cfg = config(min_oi_change_interval=-10000)
    down = row(snap(112000), snap(125000, minutes_ago=5), Snapshot(), cfg)
    up = row(snap(138000), snap(125000, minutes_ago=5), Snapshot(), cfg)
    assert passes(down, cfg)
    assert not passes(up, cfg)


def test_option_type_filter_applies():
    cfg = config(option_type="PE")
    call = build_row(contract(25000, "CE"), snap(1000), snap(900, minutes_ago=5), Snapshot(), cfg, LIVE, 25000.0, 50.0)
    put = build_row(contract(25000, "PE"), snap(1000), snap(900, minutes_ago=5), Snapshot(), cfg, LIVE, 25000.0, 50.0)
    assert not passes(call, cfg)
    assert passes(put, cfg)


def test_strike_window_is_centred_on_atm():
    strikes = [24800, 24850, 24900, 24950, 25000, 25050, 25100]
    assert window_strikes(strikes, 24950, 1, 1) == [24900, 24950, 25000]
    assert window_strikes(strikes, 24950, 2, 0) == [24950, 25000, 25050]


def test_summary_reports_largest_moves_per_option_type():
    cfg = config()
    rows = [
        build_row(contract(25100, "CE"), snap(1000), snap(500, minutes_ago=5), Snapshot(), cfg, LIVE, 25000.0, 50.0),
        build_row(contract(25200, "CE"), snap(500), snap(1200, minutes_ago=5), Snapshot(), cfg, LIVE, 25000.0, 50.0),
        build_row(contract(25000, "PE"), snap(2000), snap(800, minutes_ago=5), Snapshot(), cfg, LIVE, 25000.0, 50.0),
        build_row(contract(24900, "PE"), snap(300), snap(900, minutes_ago=5), Snapshot(), cfg, LIVE, 25000.0, 50.0),
    ]
    found = summarise(rows)
    assert found["largest_ce_increase"]["strike"] == 25100
    assert found["largest_ce_decrease"]["strike"] == 25200
    assert found["largest_pe_increase"]["strike"] == 25000
    assert found["largest_pe_decrease"]["strike"] == 24900


def test_top_movers_splits_additions_from_reductions():
    cfg = config()
    rows = [
        build_row(contract(25000 + step * 50, "CE", token=step), snap(1000 + step * 100), snap(1000, minutes_ago=5), Snapshot(), cfg, LIVE, 25000.0, 50.0)
        for step in range(-3, 4)
    ]
    movers = top_movers(rows, limit=2)
    assert len(movers["additions"]) == 2
    assert movers["additions"][0]["oi_change"] >= movers["additions"][1]["oi_change"]
    assert len(movers["reductions"]) == 2
    assert movers["reductions"][0]["oi_change"] <= movers["reductions"][1]["oi_change"]


def test_summary_ignores_rows_without_a_computed_change():
    rows = [row(snap(1000), Snapshot(), Snapshot())]
    found = summarise(rows)
    assert found["largest_ce_increase"] is None
    assert top_movers(rows) == {"additions": [], "reductions": []}


def test_strategy_screen_interpretation_still_reads_as_possible():
    from app.domain.strike_selection import oi_interpretation

    assert "possible long buildup" in oi_interpretation(10, 5000)
    assert "incomplete" in oi_interpretation(None, 5000)


def test_scanner_module_never_places_orders():
    from pathlib import Path

    for name in ("domain/oi_scanner.py", "services/oi_scanner.py"):
        text = (Path(__file__).resolve().parents[1] / "app" / name).read_text()
        assert "place_order" not in text
        assert "kiteconnect" not in text
