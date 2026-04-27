"""Structural tests for the 19.0.1.0.1 ROQ config-parameter backfill migration.

These are pure-Python tests — they do not need a live Odoo runtime. They
guard the invariant that the migration's ``ROQ_DEFAULTS`` dict stays in
sync with ``data/ir_config_parameter_data.xml`` (the source of truth).

If a future change adds, removes, or modifies a row in the XML file
without updating the migration script, these tests fail loudly so the
two files cannot drift apart silently.
"""
import ast
import pathlib
import xml.etree.ElementTree as ET

import pytest


_MODULE_ROOT = pathlib.Path(__file__).resolve().parents[1]
_XML_PATH = _MODULE_ROOT / 'data' / 'ir_config_parameter_data.xml'
_MIGRATION_PATH = (
    _MODULE_ROOT / 'migrations' / '19.0.1.0.1' / 'post-migration.py'
)


def _extract_migration_constants():
    """Parse the migration script with ``ast`` to pull out ``ROQ_DEFAULTS``
    and the ``migrate`` function signature without executing the module.

    We can't ``importlib`` the file because it imports from ``odoo`` (a real
    runtime dependency that the conftest stubs do not fully provide — e.g.
    ``odoo.SUPERUSER_ID``). Static parsing is the right tool here: this
    test is structural, not behavioural, so no execution is needed.

    Returns:
        ``(roq_defaults_dict, migrate_param_names_list)``.
    """
    source = _MIGRATION_PATH.read_text(encoding='utf-8')
    tree = ast.parse(source, filename=str(_MIGRATION_PATH))

    roq_defaults = None
    migrate_params = None

    for node in tree.body:
        # Top-level: ROQ_DEFAULTS = { ... }
        if (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
            and node.targets[0].id == 'ROQ_DEFAULTS'
        ):
            roq_defaults = ast.literal_eval(node.value)
        # Top-level: def migrate(cr, version): ...
        elif isinstance(node, ast.FunctionDef) and node.name == 'migrate':
            migrate_params = [arg.arg for arg in node.args.args]

    if roq_defaults is None:
        pytest.fail(
            f"Could not find top-level ROQ_DEFAULTS dict in {_MIGRATION_PATH}"
        )
    if migrate_params is None:
        pytest.fail(
            f"Could not find top-level migrate() function in {_MIGRATION_PATH}"
        )
    return roq_defaults, migrate_params


def _parse_xml_params():
    """Return ``{key: value}`` for every <record model="ir.config_parameter">
    in the seed data file."""
    tree = ET.parse(_XML_PATH)
    root = tree.getroot()
    params = {}
    for record in root.findall("record[@model='ir.config_parameter']"):
        key_field = record.find("field[@name='key']")
        value_field = record.find("field[@name='value']")
        assert key_field is not None and key_field.text, (
            f"Record {record.get('id')!r} missing <field name='key'>"
        )
        assert value_field is not None and value_field.text is not None, (
            f"Record {record.get('id')!r} missing <field name='value'>"
        )
        params[key_field.text.strip()] = value_field.text.strip()
    return params


@pytest.fixture(scope='module')
def xml_params():
    return _parse_xml_params()


@pytest.fixture(scope='module')
def migration_constants():
    return _extract_migration_constants()


@pytest.fixture(scope='module')
def migration_defaults(migration_constants):
    return migration_constants[0]


@pytest.fixture(scope='module')
def migrate_params(migration_constants):
    return migration_constants[1]


def test_migration_file_exists():
    assert _MIGRATION_PATH.is_file(), (
        f"Expected post-migration script at {_MIGRATION_PATH}"
    )


def test_xml_data_file_exists():
    assert _XML_PATH.is_file(), (
        f"Expected seed data file at {_XML_PATH}"
    )


def test_xml_has_at_least_one_param(xml_params):
    # Sanity: parsing actually found rows. If the XML schema ever
    # changes, this catches the silent failure mode.
    assert len(xml_params) > 0, "No ir.config_parameter records found in XML"


def test_every_xml_key_is_in_migration_defaults(xml_params, migration_defaults):
    missing = sorted(set(xml_params) - set(migration_defaults))
    assert not missing, (
        "Migration ROQ_DEFAULTS is missing keys present in the XML data file: "
        f"{missing}. Update post-migration.py to mirror the XML."
    )


def test_no_extra_keys_in_migration_defaults(xml_params, migration_defaults):
    extra = sorted(set(migration_defaults) - set(xml_params))
    assert not extra, (
        "Migration ROQ_DEFAULTS contains keys NOT present in the XML data file: "
        f"{extra}. Either remove them from post-migration.py or add corresponding "
        "<record> entries to data/ir_config_parameter_data.xml."
    )


def test_migration_values_match_xml_values(xml_params, migration_defaults):
    mismatches = {
        key: (xml_params[key], migration_defaults[key])
        for key in xml_params
        if key in migration_defaults
        and xml_params[key] != migration_defaults[key]
    }
    assert not mismatches, (
        "Migration ROQ_DEFAULTS values do not match XML seed values "
        "(format: {key: (xml_value, migration_value)}): "
        f"{mismatches}"
    )


def test_all_keys_use_roq_namespace(migration_defaults):
    # All ROQ system defaults live under the 'roq.' namespace by
    # convention. A stray non-roq key would be a programming error.
    non_roq = sorted(k for k in migration_defaults if not k.startswith('roq.'))
    assert not non_roq, (
        f"ROQ_DEFAULTS contains keys outside the 'roq.' namespace: {non_roq}"
    )


def test_all_values_are_strings(migration_defaults):
    # ir.config_parameter stores text — set_param will coerce, but
    # storing strings up-front avoids surprises when get_param returns
    # the value back as a string in ROQ runtime code.
    non_string = {
        k: type(v).__name__
        for k, v in migration_defaults.items()
        if not isinstance(v, str)
    }
    assert not non_string, (
        f"ROQ_DEFAULTS contains non-string values (would confuse get_param): "
        f"{non_string}"
    )


def test_migrate_function_signature(migrate_params):
    """Odoo's migration loader calls ``migrate(cr, version)``. Ensure
    the signature did not drift. Verified via static AST parse rather
    than import (the migration imports ``odoo.SUPERUSER_ID``, which is
    not part of the test stub surface)."""
    assert migrate_params == ['cr', 'version'], (
        f"migrate() signature must be (cr, version); got {migrate_params}"
    )
