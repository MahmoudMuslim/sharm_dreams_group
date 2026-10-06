"""Verifies the *live* seeded tables, as opposed to the fixtures used by the
unit tests in the two `doctype/*/test_*.py` files.

Run: bench --site <site> execute sharm_dreams_group.seed_checks.run
"""

from __future__ import annotations

import json
import pathlib

import frappe

from sharm_dreams_group.seeder import TAX_SLAB_NAME
from sharm_dreams_group.sharm_dreams_group.doctype.sdg_gross_insurance_band.sdg_gross_insurance_band import (
	SDGGrossInsuranceBand,
)

SEED_FILE = pathlib.Path(__file__).resolve().parent.parent / "seed_data" / "seed.json"

failures: list[str] = []


def _check(label: str, got, want) -> None:
	ok = got == want
	print(f"  {'PASS' if ok else 'FAIL'}  {label:56} got={got!s:>12}  want={want}")
	if not ok:
		failures.append(label)


def run() -> None:
	print("=== live SDG Gross Insurance Band table ===")
	rows = frappe.get_all(
		"SDG Gross Insurance Band",
		filters={"active": 1},
		fields=["name", "from_gross", "to_gross"],
		order_by="from_gross asc",
	)
	seed = json.loads(SEED_FILE.read_text())

	_check("active band count matches the seed file", len(rows), len(seed["gross_insurance_bands"]))
	_check(
		"lowest band matches the lowest observed gross",
		f"{rows[0].from_gross:,.2f}",
		f"{min(b['from_gross'] for b in seed['gross_insurance_bands']):,.2f}",
	)

	# contiguity: a hole would silently drop an employee onto the wrong premium
	from itertools import pairwise

	holes = [
		(a.name, b.name)
		for a, b in pairwise(rows)
		if round(float(a.to_gross), 2) != round(float(b.from_gross), 2)
	]
	_check("no holes in the live table", holes, [])

	# every gross the legacy workbook actually used must resolve to its own band
	unresolved = []
	for band in seed["gross_insurance_bands"]:
		got = SDGGrossInsuranceBand.lookup(band["from_gross"])
		if got is None or round(float(got.from_gross), 2) != round(float(band["from_gross"]), 2):
			unresolved.append(band["from_gross"])
	_check(f"all {len(seed['gross_insurance_bands'])} observed grosses resolve", len(unresolved), 0)

	fallback = SDGGrossInsuranceBand.lookup(1)
	_check("a gross below the table still resolves", fallback is not None, True)
	if fallback:
		print(f"         falls back to {fallback.name} (from {fallback.from_gross:,.2f})")

	print("=== live Income Tax Slab bands ===")
	bands = frappe.get_all(
		"Taxable Salary Slab",
		filters={"parenttype": "Income Tax Slab", "parent": TAX_SLAB_NAME},
		fields=["to_amount", "percent_deduction", "sdg_fixed_amount"],
		order_by="to_amount asc",
	)
	_check("seven bands", len(bands), 7)
	_check(
		"the open-ended band carries the top rate",
		bands[-1]["percent_deduction"] if bands else 0,
		max((b["percent_deduction"] for b in bands), default=0),
	)
	_check(
		"bands run from zero to the top",
		f"{bands[0]['to_amount']:.0f}" if bands else "",
		"40000",
	)

	print()
	if failures:
		print(f"{len(failures)} CHECK(S) FAILED:")
		for f in failures:
			print(f"  - {f}")
	else:
		print("live seeded data is consistent")
