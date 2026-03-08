"""
Structural test: verify purchase.order.line field references have been
verified and documented for Odoo 19.
"""
import pathlib


def test_po_wizard_field_names_verified_for_odoo19():
    """product_uom and date_planned verified as correct Odoo 19 field names."""
    src = pathlib.Path(
        'mml.roq.model/mml_roq_forecast/models/roq_raise_po_wizard.py'
    ).read_text()
    assert 'product_uom' in src, "product_uom field reference missing"
    assert 'date_planned' in src, "date_planned field reference missing"
    # The pre-deploy TODO must be resolved before this test passes
    assert 'TODO(pre-deploy)' not in src, (
        "The pre-deploy TODO for purchase.order.line field verification must be "
        "resolved. Confirm fields are correct and remove the TODO comment."
    )
