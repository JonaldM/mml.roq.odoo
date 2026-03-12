"""Structural tests — verify year view components exist in JS/SCSS/XML."""
import pathlib

ROOT = pathlib.Path(__file__).parent.parent

def test_scss_has_year_grid():
    src = (ROOT / 'static/src/scss/shipment_calendar.scss').read_text(encoding='utf-8')
    assert '.mml-sg-year-grid' in src, "SCSS must define .mml-sg-year-grid"
    assert '.mml-sg-year-cell' in src, "SCSS must define .mml-sg-year-cell"
    assert '.mml-sg-year-state-badge' in src, "SCSS must define .mml-sg-year-state-badge"

def test_js_has_year_components():
    src = (ROOT / 'static/src/js/shipment_calendar_view.js').read_text(encoding='utf-8')
    assert 'YearMonthCell' in src, "JS must define YearMonthCell component"
    assert 'ShipmentYearRenderer' in src, "JS must define ShipmentYearRenderer component"
    assert 'zoomLevel' in src, "JS must include zoomLevel state"
    assert 'onDrillDown' in src, "JS must include onDrillDown method"
    assert 'onBackToYear' in src, "JS must include onBackToYear method"
    assert 'yearOffset' in src, "JS must include yearOffset state"

def test_xml_has_year_templates():
    src = (ROOT / 'static/src/xml/shipment_calendar.xml').read_text(encoding='utf-8')
    assert 'ShipmentYearRenderer' in src, "XML must have ShipmentYearRenderer template"
    assert 'YearMonthCell' in src, "XML must have YearMonthCell template"
    assert 'onBackToYear' in src, "XML must wire onBackToYear"
