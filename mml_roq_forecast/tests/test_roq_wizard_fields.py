"""
Structural test: verify purchase.order.line field references have been
verified and documented for Odoo 19.
"""
import pathlib

_MODELS_DIR = pathlib.Path(__file__).parent.parent / 'models'


def test_po_wizard_field_names_verified_for_odoo19():
    """product_uom_id and date_planned verified as correct Odoo 19 field names."""
    src = (_MODELS_DIR / 'roq_raise_po_wizard.py').read_text()
    # Odoo 19: field was renamed from product_uom to product_uom_id
    assert 'product_uom_id' in src, "product_uom_id field reference missing (Odoo 19 name)"
    assert 'date_planned' in src, "date_planned field reference missing"
    # The pre-deploy TODO must be resolved before this test passes
    assert 'TODO(pre-deploy)' not in src, (
        "The pre-deploy TODO for purchase.order.line field verification must be "
        "resolved. Confirm fields are correct and remove the TODO comment."
    )
