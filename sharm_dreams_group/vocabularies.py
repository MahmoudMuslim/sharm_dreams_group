# Copyright (c) 2026, Sharm Dreams Group and contributors
# For license information, please see license.txt

"""Reference data seeded from the master workbook.

Three vocabularies that the Employee import links to, plus the Designation
records themselves.  Without these the Link fields are empty dropdowns.

Run: bench --site <site> execute sharm_dreams_group.vocabularies.seed
     bench --site <site> execute sharm_dreams_group.vocabularies.report
"""

from __future__ import annotations

import collections
import json
import pathlib

import frappe

MASTER = pathlib.Path(__file__).resolve().parent.parent / "seed_data" / "master_reference.json"

# Sub Department values in the master that are really locations.  The column
# conflates the two, and flagging them keeps the judgement visible to HR instead
# of silently classifying "Head Office" as a sub-department.
LOOKS_LIKE_A_LOCATION = {
	"Head Office",
	"Sharm El Sheikh",
	"Marsa Alam",
	"Red Sea",
	"Dokki",
	"@40",
	"@40 Villa 41",
	"Villa 644",
	"Head Office/Katamya",
	"Head Office/Ayady",
	"Nile Citadel Boat",
	"Abu Dabbab",
	"Katamya",
	"Hurghada",
}

VOCABULARIES = {
	"SDG Employment Category": ("category_name", "category_name_ar"),
	"SDG Contract Type": ("contract_type_name", "contract_type_name_ar"),
	"SDG Sub Department": ("sub_department_name", "sub_department_name_ar"),
}


def load() -> dict:
	if not MASTER.exists():
		raise FileNotFoundError(f"{MASTER} not found. Run tools/build_master_fixture.py on the host first.")
	return json.loads(MASTER.read_text())


def resolve_existing(doctype: str, name: str) -> str | None:
	"""Find an existing row by name, tolerating case differences.

	The site already holds 197 curated Designation records, and 17 of the master's
	106 titles differ from them only in capitalisation ("Administrative
	assistant" vs "Administrative Assistant").  An exact lookup would treat each
	of those as new, so the comparison is case-insensitive and the existing row is
	reused.
	"""
	exact = frappe.db.get_value(doctype, name, "name")
	if exact:
		return exact
	all_names = frappe.get_all(doctype, fields=["name"], limit_page_length=0, pluck="name")
	for candidate in all_names:
		if candidate.lower() == name.lower():
			return candidate
	return None


def _upsert(doctype: str, name_field: str, name: str, values: dict, protect_name: bool = False) -> str:
	"""Create the row, or update it only if something actually differs.

	Returns "created", "updated" or "unchanged".  Counting an update as a create
	previously made a second run report 17 new Designations when nothing had been
	added, which is exactly the kind of report that stops being trusted.

	`protect_name` leaves an existing row's own naming field alone.  Designation
	names the document (`autoname: field:designation_name`), so writing a
	differently-cased value never converges - the save is rejected and the row
	reports as changed on every run forever.

	A desired value of None means "the source has nothing for this column", not
	"clear it".  The master lists some titles twice, differing only in
	capitalisation - "Administrative Assistant" as a current position with an
	Arabic name, "Administrative assistant" as a starting position without one -
	and both resolve to the same row.  Blanking on the second would fight the
	first on every run, so a seed fills fields and never empties them.
	"""
	existing = resolve_existing(doctype, name)
	if not existing:
		frappe.get_doc({"doctype": doctype, name_field: name, **values}).insert(ignore_permissions=True)
		return "created"

	doc = frappe.get_doc(doctype, existing)
	desired = {k: v for k, v in values.items() if k != name_field} if protect_name else values
	desired = {k: v for k, v in desired.items() if not (v is None and doc.get(k))}
	if all(doc.get(k) == v for k, v in desired.items()):
		return "unchanged"
	doc.update(desired)
	doc.save(ignore_permissions=True)
	return "updated"


def seed() -> None:
	"""Create the vocabulary rows and the Designation records."""
	data = load()

	def tally(doctype, name_field, ar_field, items, extra=None, protect_name=False):
		tally_ = collections.Counter()
		for item in items:
			values = {
				name_field: item["name"],
				ar_field: item.get("name_ar") or None,
			}
			if extra:
				values.update(extra(doctype, item))
			tally_[_upsert(doctype, name_field, item["name"], values, protect_name)] += 1
		frappe.db.commit()
		print(
			f"  {doctype:28} {tally_['created']:>3} created, {tally_['updated']:>3} updated,"
			f" {tally_['unchanged']:>3} unchanged  (of {len(items)})"
		)

	for doctype, (name_field, ar_field) in VOCABULARIES.items():
		tally(
			doctype,
			name_field,
			ar_field,
			data["vocabularies"].get(doctype, []),
			extra=lambda dt, item: (
				{
					"source": "Seeded from Master Colleagues Data",
					"active": 1,
					"looks_like_a_location": 1 if item["name"] in LOOKS_LIKE_A_LOCATION else 0,
				}
				if dt == "SDG Sub Department"
				else {"source": "Seeded from Master Colleagues Data", "active": 1}
			),
		)

	tally(
		"Designation",
		"designation_name",
		"sdg_designation_name_ar",
		data["designations"],
		extra=lambda _dt, item: {"description": item.get("note") or None},
		protect_name=True,
	)

	print()
	report()


def report() -> None:
	"""Print what was seeded, and flag the rows that need a human decision."""
	print("=== SDG Employment Category ===")
	for r in frappe.get_all("SDG Employment Category", fields=["name"], limit_page_length=0, order_by="name"):
		print(f"  {r.name}")

	print("\n=== SDG Contract Type ===")
	for r in frappe.get_all("SDG Contract Type", fields=["name"], limit_page_length=0, order_by="name"):
		print(f"  {r.name}")

	print("\n=== SDG Sub Department ===")
	for r in frappe.get_all(
		"SDG Sub Department",
		fields=["name", "looks_like_a_location"],
		limit_page_length=0,
		order_by="name",
	):
		flag = "   <== looks like a location, not a sub-department" if r.looks_like_a_location else ""
		print(f"  {r.name}{flag}")

	print("\n=== Designation, and the 5 with more than one Arabic spelling ===")
	rows = frappe.get_all(
		"Designation",
		filters={"name": ("like", "%")},
		fields=["name", "sdg_designation_name_ar"],
		limit_page_length=0,
		order_by="name",
	)
	with_ar = [r for r in rows if r.sdg_designation_name_ar]
	print(f"  total designations : {len(rows)}")
	print(f"  with an Arabic name: {len(with_ar)}")
	for r in with_ar:
		print(f"    {r.name:46} <- {r.sdg_designation_name_ar}")
