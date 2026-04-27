# feat(roq): backfill ir.config_parameter defaults on upgrade

## Why

`mml_roq_forecast/data/ir_config_parameter_data.xml` declares 15 ROQ system
defaults (`roq.default_lead_time_days`, `roq.default_service_level`,
`roq.lookback_weeks`, etc.), but the file is `<odoo noupdate="1">`. That
means it only takes effect on **first install** — module upgrades (`-u`) do
not reseed `noupdate=1` records.

A QA sweep on the dev DB (mml_test_sprint, 2026-03-11) found that several
of these keys were missing on already-installed instances, because the
module was originally installed before the data file existed.

This PR closes audit finding #5 from the 2026-04-27 production-readiness
review.

## What changed

1. **`mml_roq_forecast/__manifest__.py`** — version bump
   `19.0.1.0.0` → `19.0.1.0.1` so Odoo's migration framework picks up the
   new script on the next `-u`.

2. **`mml_roq_forecast/migrations/19.0.1.0.1/post-migration.py`** *(new)* —
   idempotent backfill. Walks `ROQ_DEFAULTS`, calls
   `ir.config_parameter.get_param(key)` for each, and only writes when the
   value is missing. Logs the keys it backfilled (or a no-op message if the
   instance was already complete). Skips work entirely on fresh installs
   (`version is None` — the data file handles seeding in that case).

   Standard Odoo migration signature: `def migrate(cr, version)` with
   `env = api.Environment(cr, SUPERUSER_ID, {})` constructed inside.

3. **`mml_roq_forecast/tests/test_config_param_backfill.py`** *(new)* —
   pure-Python structural tests (9 cases, no Odoo runtime required) that
   parse both files via `ast` + `xml.etree` and assert:
   - migration file & XML data file both exist
   - every XML key is present in `ROQ_DEFAULTS`
   - no extra keys in `ROQ_DEFAULTS` that aren't in the XML
   - all values match between the two sources
   - all keys live under the `roq.` namespace
   - all values are strings (matches `ir.config_parameter` storage)
   - `migrate()` signature is exactly `(cr, version)`

   These are tripwires against silent drift: any future change to the XML
   that isn't mirrored in the migration script will fail the suite loudly.

## Idempotency & safety

- **Read-before-write** on every key — re-running the upgrade is a no-op
  if all keys are already present.
- **No mutation of existing values** — only fills gaps, never overwrites
  operator-customised settings.
- **Logs an info line** when work is done, and a separate info line when
  nothing was needed, so audit trails are clear.
- **Fresh installs short-circuit** before constructing the environment —
  the data file already handled seeding, so we don't re-do it.

## Test plan

- [x] `pytest -m "not odoo_integration" -q` → **117 passed** (was 108 before;
  9 new structural tests, 0 regressions).
- [x] Negative-control verified by temporarily corrupting one value in
  `ROQ_DEFAULTS` — `test_migration_values_match_xml_values` flagged it
  immediately.
- [ ] After merge: run `odoo-bin -u mml_roq_forecast -d <staging_db>` on a
  staging clone of the live Hetzner DB; verify the log line "backfilled N
  missing ROQ defaults" matches the keys QA flagged on 2026-03-11.
- [ ] Confirm post-upgrade that
  `env['ir.config_parameter'].sudo().get_param('roq.lookback_weeks')`
  returns `'156'` (and similar for other QA-flagged keys).
- [ ] Re-run the upgrade a second time on the same DB; verify the log line
  reports "all 15 ROQ defaults already present" (idempotency check).

## Risk

Low. The migration only writes missing keys, never touches existing ones,
and runs in seconds. If the script were to fail mid-loop, partial state is
still better than the current "missing keys" baseline, and the next upgrade
would finish the job. Worst-case rollback: revert to 19.0.1.0.0; the data
file remains untouched and existing operator-set values remain in place.
