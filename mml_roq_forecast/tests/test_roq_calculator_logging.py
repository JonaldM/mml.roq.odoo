"""Structural test: verify weeks-of-cover sentinel return is logged."""
import pathlib

_SERVICES_DIR = pathlib.Path(__file__).parent.parent / 'services'


def test_sentinel_return_is_logged():
    src = (_SERVICES_DIR / 'roq_calculator.py').read_text()
    assert '_logger' in src, "roq_calculator.py must use _logger"
    assert '999' in src, "Sentinel value 999.0 must be present"
    # After the fix, a log call must appear near the sentinel return
    assert '.debug(' in src or '.warning(' in src, (
        "Sentinel return must be accompanied by a log call"
    )
