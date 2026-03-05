import ast
import os
import pytest


def test_roq_port_has_no_name_get():
    """name_get() must not exist — removed in Odoo 18."""
    src_path = os.path.join(os.path.dirname(__file__), '..', 'models', 'roq_port.py')
    with open(src_path, encoding='utf-8') as f:
        source = f.read()
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == 'name_get':
            pytest.fail("roq_port.py still defines name_get() — must use _compute_display_name()")


def test_roq_port_has_compute_display_name():
    """_compute_display_name() must exist with @api.depends('code', 'name')."""
    src_path = os.path.join(os.path.dirname(__file__), '..', 'models', 'roq_port.py')
    with open(src_path, encoding='utf-8') as f:
        source = f.read()
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == '_compute_display_name':
            # Verify @api.depends decorator present
            for dec in node.decorator_list:
                if isinstance(dec, ast.Call):
                    func = dec.func
                    if isinstance(func, ast.Attribute) and func.attr == 'depends':
                        args = [a.value for a in dec.args if isinstance(a, ast.Constant)]
                        assert 'code' in args, f"@api.depends must include 'code', got {args}"
                        assert 'name' in args, f"@api.depends must include 'name', got {args}"
                        return
            pytest.fail("_compute_display_name found but missing @api.depends('code', 'name')")
    pytest.fail("_compute_display_name() not found in roq_port.py")
