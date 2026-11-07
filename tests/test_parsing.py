import pytest

from parsing import parse_date_percentiles, parse_options, parse_percentiles, parse_probability


def test_probability_takes_last_line():
    assert parse_probability("Probability: 40% at first\n...\n**Probability: 12.5%**") == 0.125


def test_probability_missing_raises():
    with pytest.raises(ValueError):
        parse_probability("I think it is quite likely.")


def test_options_normalised_and_exact_names():
    text = "Option_A...\n1: 10%\n10: 30%\nMore than 10: 60%\n"
    got = parse_options(text, ["1", "10", "More than 10"])
    assert got == pytest.approx({"1": 0.1, "10": 0.3, "More than 10": 0.6})


def test_options_missing_one_raises():
    with pytest.raises(ValueError):
        parse_options("Democratic Party: 50%\n", ["Democratic Party", "Republican Party"])


def test_options_bad_sum_raises():
    with pytest.raises(ValueError):
        parse_options("A: 80%\nB: 80%\n", ["A", "B"])


def test_numeric_percentiles_with_commas_and_units():
    text = "\n".join(f"Percentile {p}: ${v:,} million" for p, v in [(10, 1000), (20, 1200), (40, 1500), (60, 1700), (80, 2100), (90, 2500)])
    assert parse_percentiles(text)[0] == (0.1, 1000.0)
    assert parse_percentiles(text)[-1] == (0.9, 2500.0)


def test_numeric_not_increasing_raises():
    text = "\n".join(f"Percentile {p}: {v}" for p, v in [(10, 5), (20, 4), (40, 6), (60, 7), (80, 8), (90, 9)])
    with pytest.raises(ValueError):
        parse_percentiles(text)


def test_dates():
    text = "\n".join(f"Percentile {p}: 2027-0{i + 1}-15" for i, p in enumerate([10, 20, 40, 60, 80, 90]))
    out = parse_date_percentiles(text)
    assert len(out) == 6 and out[0][1] < out[-1][1]
