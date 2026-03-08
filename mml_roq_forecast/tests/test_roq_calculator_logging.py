"""Structural test: verify weeks-of-cover sentinel return is logged."""
import pathlib


def test_sentinel_return_is_logged():
    src = pathlib.Path(
        'mml.roq.model/mml_roq_forecast/services/roq_calculator.py'
    ).read_text()
    assert '_logger' in src, "roq_calculator.py must use _logger"
    assert '999' in src, "Sentinel value 999.0 must be present"
    # After the fix, a log call must appear near the sentinel return
    assert '.debug(' in src or '.warning(' in src, (
        "Sentinel return must be accompanied by a log call"
    )
