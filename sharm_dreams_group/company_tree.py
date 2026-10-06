# Copyright (c) 2026, Sharm Dreams Group and contributors
# For license information, please see license.txt

"""Company filters mean "this company and everything under it".

`Sharm Dreams Group` is the group parent: `is_group = 1`, no parent of its own,
and all 13 operating companies hang off it.  It holds no data of its own - zero
employees, zero salary slips, zero invoices.  So the stock behaviour, where a
Company filter is an equality on `company`, returns nothing at all for the
parent: `company = 'Sharm Dreams Group'` matches no row on any company-scoped
doctype.

That makes the group parent useless as a reporting choice, which is the one
value a manager actually wants: the whole group.

So a Company filter is widened to the selected company plus its descendants.
For a leaf company the descendant list is empty and the filter is left as a
plain equality, so single-company filtering is unchanged - only the parent
behaves differently, and only in the direction that was previously empty.

Run with:
    bench --site <site> execute sharm_dreams_group.company_tree.report
"""

from __future__ import annotations

import frappe
from frappe.utils.nestedset import get_descendants_of

# Cached per request rather than in Redis: the tree only changes when a Company
# is created or re-parented, and a stale value for the length of one request is
# not a correctness problem worth a cache invalidation to avoid.  `frappe.local`
# is per request, which is exactly the lifetime wanted here.
_CACHE_ATTR = "_sdg_company_subtrees"


def subtree(company: str | None) -> list[str]:
	"""`[company, *descendants]`, or `[]` for a blank company.

	An empty result means "do not filter", which is what an unset Company should
	do - it is not the same as filtering on the parent.
	"""
	company = (company or "").strip()
	if not company:
		return []

	cache = getattr(frappe.local, _CACHE_ATTR, None)
	if cache is None:
		cache = {}
		setattr(frappe.local, _CACHE_ATTR, cache)
	if company not in cache:
		cache[company] = [company, *get_descendants_of("Company", company)]
	return cache[company]


def has_descendants(company: str | None) -> bool:
	"""True when widening this company would actually change the result set."""
	company = (company or "").strip()
	if not company or not frappe.db.exists("Company", company):
		return False
	return bool(get_descendants_of("Company", company))


def expand(filters):
	"""Widen any Company filter in `filters`, preserving its shape.

	Handles both shapes a desk widget uses: a report's flat
	`{"company": <value>}` and a doctype's
	`[[doctype, "company", "=", <value>]]` rows.  Anything else is returned
	untouched.
	"""
	if isinstance(filters, dict):
		if "company" not in filters:
			return filters
		widened = _widen(filters["company"])
		return filters if widened is None else {**filters, "company": widened}

	if isinstance(filters, list):
		changed = False
		rows = []
		for row in filters:
			if isinstance(row, list | tuple) and len(row) == 4 and row[1] == "company":
				value = row[3]
				if isinstance(value, str) and has_descendants(value):
					row = [row[0], row[1], "in", subtree(value)]
					changed = True
			rows.append(row)
		return rows if changed else filters

	return filters


def _widen(value):
	"""The replacement value for a report-style `{"company": value}`, or None.

	None means "leave it alone" - either there is nothing to widen, or the value
	is not a plain company name (a list from an earlier expansion, a `%` pattern
	like `Sharm Dreams%`, a comparison tuple the caller already built).
	"""
	if not isinstance(value, str) or not has_descendants(value):
		return None
	# A report's filter dict wants the (operator, values) pair; a bare list would
	# be read as that pair's first element and blow up in apply_dict_filters.
	return ["in", subtree(value)]


@frappe.whitelist()
def get_company_tree() -> dict:
	"""`{company: [company, *descendants]}` for every company.

	Served to the desk so a widget filter can be widened in the browser, where
	`get_all_filters` builds it.  The tree is small (14 companies) and only
	changes when a Company is re-parented, so it is sent whole.
	"""
	companies = frappe.get_all("Company", pluck="name", limit_page_length=0)
	return {name: subtree(name) for name in sorted(companies)}


def report() -> None:
	"""Print the tree and what each company widens to."""
	tree = get_company_tree()
	print(f"=== company tree: {len(tree)} companies ===")
	for parent, members in tree.items():
		flag = "is_group" if len(members) > 1 else "leaf"
		print(f"  {parent}  [{flag}, {len(members)}]")
		for child in members[1:]:
			print(f"      - {child}")
	print()
	group = "Sharm Dreams Group"
	widened = tree.get(group, [])
	print(f"filtering by {group!r} as an equality would match no rows on any")
	print(f"company-scoped doctype; widened it covers {len(widened)} companies.")
	for doctype in ("Employee", "Salary Slip", "Sales Invoice", "Purchase Invoice"):
		if not frappe.get_meta(doctype).has_field("company"):
			continue
		equal = frappe.db.count(doctype, {"company": group})
		wide = frappe.db.count(doctype, {"company": ("in", widened)})
		print(f"    {doctype:<20} equality={equal:<6} widened={wide}")
