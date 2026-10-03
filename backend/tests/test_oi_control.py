from app.domain.oi_control import around_atm, combined, concentration, difference_percent, sum_raw


def test_totals_use_raw_oi_not_rounded_lakhs():
    call = sum_raw([8_826_805, 8_124_000])
    put = sum_raw([7_635_000, 9_152_000])
    assert call == 16_950_805
    assert put == 16_787_000
    assert call - put == 163_805


def test_difference_percent_matches_the_specified_formula():
    call = 32_728_000
    put = 33_942_000
    assert round(difference_percent(call, put), 2) == -1.82


def test_small_difference_is_balanced_against_the_threshold():
    threshold = 5 * 100_000
    assert concentration(-210_000, threshold) == "BALANCED"
    assert concentration(-600_000, threshold) == "PUT"
    assert concentration(600_000, threshold) == "CALL"


def test_mixed_when_structure_and_flow_disagree():
    assert combined("CALL", "PUT") == "MIXED"
    assert combined("PUT", "PUT") == "PUT"
    assert combined("BALANCED", "PUT") == "PUT"


def test_atm_plus_five_takes_eleven_listed_strikes():
    strikes = [22000 + 50 * i for i in range(30)]
    window = around_atm(strikes, 22700, 5)
    assert window[0] == 22450
    assert window[-1] == 22950
    assert len(window) == 11
