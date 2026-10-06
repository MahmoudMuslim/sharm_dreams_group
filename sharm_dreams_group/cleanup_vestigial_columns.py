"""One-off cleanup: drop the vestigial column left by a removed custom field.

Frappe deliberately keeps the DB column when a Custom Field is deleted, so this
site carries `tabEmployee.sdg_position_arabic` even though the field is gone from
the meta.  The column was created today, never held data, and a fresh install
never creates it, so it is safe to drop.

Run: bench --site <site> execute sharm_dreams_group.cleanup_vestigial_columns
"""

from __future__ import annotations

import frappe

# fieldname -> doctype
VESTIGIAL = {
	"sdg_position_arabic": "Employee",
	"sdg_grade_level": "Employee",
	# Superseded by sdg_social_insurance_section when the section was renamed.
	"sdg_medical_insurance_section": "Employee",
}


def run() -> None:
	for fieldname, dt in VESTIGIAL.items():
		table = f"tab{dt}"
		columns = frappe.db.get_table_columns(dt)

		if frappe.db.exists("Custom Field", {"dt": dt, "fieldname": fieldname}):
			print(f"  skip {dt}.{fieldname}: still a live custom field")
			continue
		if fieldname not in columns:
			print(f"  skip {dt}.{fieldname}: no column")
			continue
		if frappe.db.count(dt):
			print(f"  skip {dt}.{fieldname}: {dt} has records, refusing to drop")
			continue

		frappe.db.sql_ddl(f"ALTER TABLE `{table}` DROP COLUMN `{fieldname}`")
		frappe.clear_cache()
		still_there = fieldname in frappe.db.get_table_columns(dt)
		print(
			f"  dropped {table}.{fieldname}" if not still_there else f"  FAILED to drop {table}.{fieldname}"
		)
