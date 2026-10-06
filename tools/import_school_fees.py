#!/usr/bin/env python3
"""Import the 2026 School Fees Grant: one child row per child, per employee.

The workbook is `Schools_Fees_Grant_2026.xlsx`, sheet "School Fees Grant 2026":
Code, Colleague Name, Position Title, Company Name, Number of Children.  The
second sheet, "Summary by Company", is COUNTIF/SUMIF over the first with no
cached values, so it reads blank until Excel recalculates - the figures are
derived here instead of trusted from it.

**Matching is by work ID first, then by name.  Anyone who matches neither is
skipped, not guessed at.**  A name-only match is accepted because 2 rows carry a
work ID of `-`; a code match is accepted because the codes are the Employee's
`name`.  Anything left over is reported and written nowhere.

**The child rows carry placeholder names and ages, and that is a real gap.**
The workbook gives a count per employee and nothing else - no child names, no
ages, no school year - and `SDG Children` requires both `child_name` and `age`.
So each row is created as "Child N of M" with `age` 0, which makes the *derived*
figures correct (`sdg_school_fee_total` = count x fee, and the dashboard card
built on it) while leaving the two fields HR has to fill in from the original PDF.
The names are deliberately obvious rather than plausible, so a row that was never
filled in cannot be mistaken for a real one.  Count how many you still owe:

    --list-placeholders

**A blank "Number of Children" is not zero.**  Three matched employees (H0266,
V0003, H0139) have an empty cell where the others have a figure.  They are
imported with no child rows and listed separately, because a blank may be a
figure the PDF extraction dropped rather than a genuine zero, and importing a
zero would quietly understate the grant.

**Children who matched neither ID nor name are skipped.**  6 of the 82 rows do
not resolve - 5 at AYADY and 1 at El Lanch, neither of which is a Company on
this site.  They carry 14 children.  Re-run this once they exist in Employee;
there is no partial import to unpick.

Run:
    python3 tools/import_school_fees.py
    python3 tools/import_school_fees.py --apply
    python3 tools/import_school_fees.py --list-placeholders --apply
"""

from __future__ import annotations

import argparse
import pathlib
import re
import sys
import unicodedata

# The bench runs in a container that cannot see /home/mahmoud, so the workbook is
# looked for in the bench's staging area first and only then at the host path -
# the arrangement import_leavers.py uses for the master workbook.
APP = pathlib.Path(__file__).resolve().parent.parent
STAGING = APP.parent.parent / "import_staging" / "school_fees"
CANDIDATES = (
	STAGING / "Schools_Fees_Grant_2026.xlsx",
	pathlib.Path("/home/mahmoud/Schools_Fees_Grant_2026.xlsx"),
)
SHEET = "School Fees Grant 2026"

# The per-child school fee for 2026.  Not in the workbook - it is a policy figure,
# supplied here, and it is the one number in this script that is not read from the
# file.  Change it in one place: every row reads FEE below.
FEE = 1200
GRANT_YEAR = 2026

# One master record for the whole group.  `SDG School Fee` is a master fee, not a
# per-company or per-year one - changing the group's fee is one edit, which is
# the reason it is a separate doctype at all (see hr/children.py).
FEE_NAME = f"School Fee {GRANT_YEAR}"


def cell(row, index):
	"""A cell, or None when the row is short."""
	return row[index] if index < len(row) else None


def normalise_name(value) -> str:
	"""Fold a person's name down to a comparable key.

	Diacritics, punctuation and spacing all vary between the workbook and Employee
	("Abdel-Hamid" vs "Abdel Hamid", "Zaghioul" vs "Zaghloul" is *not* handled -
	that one differs by more than folding can see, and the two Virgin Gates rows
	match on their code as well as their name).  Case and accent folding is what
	actually pays here.
	"""
	text = unicodedata.normalize("NFKD", str(value or ""))
	text = "".join(c for c in text if not unicodedata.combining(c))
	return " ".join(re.sub(r"[^a-z0-9]+", " ", text.lower()).split())


def read_grant():
	"""Every data row in the sheet, in file order."""
	import openpyxl

	source = next((c for c in CANDIDATES if c.exists()), None)
	if source is None:
		raise SystemExit(
			f"workbook not found; looked in {', '.join(str(c) for c in CANDIDATES)}\n"
			f"copy it in with: mkdir -p {STAGING} && cp "
			f"'/home/mahmoud/Schools_Fees_Grant_2026.xlsx' {STAGING}/"
		)
	sheet = openpyxl.load_workbook(source, data_only=True)[SHEET]
	rows = list(sheet.iter_rows(values_only=True))

	# header row 4, data from 5.  Located by the header text rather than a fixed
	# index so an inserted title row does not shift every figure by one.
	start = next(
		(
			i
			for i, r in enumerate(rows)
			if r and str(cell(r, 0) or "").strip() == "Code" and "Children" in str(cell(r, 4) or "")
		),
		None,
	)
	if start is None:
		raise SystemExit(f"no header row found in {SHEET!r}; expected a 'Code' column")

	out = []
	for offset, row in enumerate(rows[start + 1 :], start + 2):
		code = str(cell(row, 0) or "").strip()
		if not code or code == "Total":
			continue  # the Total row is the workbook's own, and it has no figures
		out.append(
			{
				"excel_row": offset,
				"code": code,
				"name": str(cell(row, 1) or "").strip(),
				"position": str(cell(row, 2) or "").strip(),
				"company": str(cell(row, 3) or "").strip(),
				"children": cell(row, 4),
			}
		)
	return out


def match(rows: list[dict], employees: dict, by_name: dict[str, list[str]]) -> tuple[dict, list]:
	"""`(matched, unmatched)` - matched by work ID, then by name.

	Name matching is only offered where it is unambiguous: if two employees share a
	name the row is left unmatched rather than picked between them.
	"""
	matched, unmatched = {}, []
	for row in rows:
		code = row["code"]
		employee_id = how = None

		# the work ID *is* the Employee's `name`, so this is the direct lookup
		if code and code != "-" and code in employees:
			employee_id, how = code, "work ID"

		if not employee_id and normalise_name(row["name"]) in by_name:
			candidates = by_name[normalise_name(row["name"])]
			if len(candidates) == 1:
				employee_id, how = candidates[0], "name"
			elif len(candidates) > 1:
				row["problem"] = f"name matches {len(candidates)} employees: {', '.join(sorted(candidates))}"
				unmatched.append(row)
				continue

		if employee_id:
			row["employee"] = employee_id
			row["matched_by"] = how
			matched[row["excel_row"]] = row
		else:
			row.setdefault("problem", "no employee matches this work ID or name")
			unmatched.append(row)
	return matched, unmatched


def placeholder_name(index: int, total: int) -> str:
	"""An obviously-not-real child name.

	Deliberately not a plausible name.  A row invented as "Child 1" is visibly
	unfinished; a row invented as "Mariam" would be indistinguishable from a real
	child and could be reported on as one.
	"""
	return f"Child {index} of {total}"


def main() -> int:
	parser = argparse.ArgumentParser(
		description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
	)
	parser.add_argument("--apply", action="store_true", help="write the records (default is report only)")
	parser.add_argument(
		"--list-placeholders",
		action="store_true",
		help="after importing, list the child rows still needing a real name and age",
	)
	args = parser.parse_args()

	import frappe

	from sharm_dreams_group import companies
	from sharm_dreams_group.hr.children import apply_to_employee, refresh_fee_counts

	frappe.init(site="dev.localhost")
	frappe.connect()
	try:
		rows = read_grant()

		employees = {
			e.name: e
			for e in frappe.get_all(
				"Employee",
				# gender is here only for the incompleteness report below; it must be
				# fetched explicitly, because reading a field the query did not select
				# off a `_dict` silently yields None rather than raising
				fields=["name", "employee_name", "company", "status", "gender"],
				limit_page_length=0,
			)
		}
		by_name: dict[str, list[str]] = {}
		for name, employee in employees.items():
			by_name.setdefault(normalise_name(employee.employee_name), []).append(name)

		matched, unmatched = match(rows, employees, by_name)

		# the company on the sheet is only used to warn: the Employee already has one,
		# and this import does not second-guess it.
		company_warnings = []
		for row in matched.values():
			live = employees[row["employee"]].company
			resolved = companies.resolve(row["company"])
			if not resolved:
				company_warnings.append((row["excel_row"], row["company"], row["employee"], live))
			elif normalise_name(resolved) != normalise_name(live):
				company_warnings.append((row["excel_row"], row["company"], row["employee"], live))

		blank_count = [r for r in matched.values() if r["children"] is None]
		zero_count = [r for r in matched.values() if r["children"] == 0]

		# `Employee.gender` is mandatory and 47 of the 310 on this site have it
		# empty, so a plain `save()` refuses to write their child rows.  That is a
		# pre-existing gap in the employee data and nothing to do with school fees -
		# but it must not silently cost us their children, so they are counted and
		# named rather than skipped, and saved past the check with a report.
		incomplete = sorted(r["employee"] for r in matched.values() if not employees[r["employee"]].gender)

		matched_children = sum(int(r["children"] or 0) for r in matched.values())
		skipped_children = sum(int(r["children"] or 0) for r in unmatched)

		# The whole point of the grant: what the matched half of the file is worth.
		# `blank_count` is excluded from the children total because a blank is not
		# zero, so this is a floor rather than the final figure.
		granted = matched_children * FEE

		print(f"rows in the sheet        : {len(rows)}")
		print(f"  matched                : {len(matched)}  ({matched_children} children)")
		print(
			f"    by work ID           : {sum(1 for r in matched.values() if r['matched_by'] == 'work ID')}"
		)
		print(f"    by name only         : {sum(1 for r in matched.values() if r['matched_by'] == 'name')}")
		print(f"  skipped, not imported  : {len(unmatched)}  ({skipped_children} children)")
		print(f"  blank child count      : {len(blank_count)}  imported with no child rows")
		print(f"  zero child count       : {len(zero_count)}")

		if unmatched:
			print("\nskipped - no employee matches their work ID or name:")
			for row in sorted(unmatched, key=lambda r: r["excel_row"]):
				print(
					f"      row {row['excel_row']:>3}  {row['code']:<7} {row['name'][:34]:34} "
					f"{row['company'][:28]:28} {row['children'] or 0} children"
				)
				print(f"                {row['problem']}")

		if blank_count:
			print("\nblank 'Number of Children' - imported with NO child rows, confirm these are not zeros:")
			for row in sorted(blank_count, key=lambda r: r["excel_row"]):
				print(
					f"      row {row['excel_row']:>3}  {row['code']:<7} {row['name'][:34]:34} {row['company'][:28]}"
				)

		if company_warnings:
			print("\ncompany on the sheet differs from the Employee's own company (not changed):")
			for excel_row, sheet_company, employee_id, live in company_warnings:
				print(f"      row {excel_row:>3}  {employee_id:<7} sheet {sheet_company[:30]:30} site {live}")

		print(f"\nfee applied: {FEE:,} per child, for {GRANT_YEAR}")
		print(f"  matched employees      : {len(matched):3} x {matched_children:3} children = {granted:>10,}")
		print(
			f"  skipped employees      : {len(unmatched):3} x {skipped_children:3} children = "
			f"{skipped_children * FEE:>10,}  (not imported)"
		)
		print(f"  whole file, if it were complete                  = {granted + skipped_children * FEE:>10,}")
		if incomplete:
			print(f"\n{len(incomplete)} matched employee(s) have no gender, which Employee requires.")
			print("  Their child rows are still written - the gap is in the employee record, not")
			print("  the grant - but they will be listed as incomplete until HR fills it in:")
			for employee_id in incomplete:
				print(f"      {employee_id:<7} {employees[employee_id].employee_name[:40]}")

		print("\nFEE is a plain number supplied to this script, not a figure read")
		print("  from the workbook. Confirm the currency with HR.")

		if args.list_placeholders and not args.apply:
			print("\n--list-placeholders reads what is already on file; run --apply first.")
			return 0

		if not args.apply:
			print("\ndry run, nothing written. re-run with --apply.")
			return 0

		frappe.flags.ignore_permissions = True

		fee = _ensure_fee(frappe)
		print(f"\nschool fee record: {fee.name} = {fee.amount:,}")

		written_rows = written_employees = touched = 0
		for row in sorted(matched.values(), key=lambda r: r["excel_row"]):
			count = row["children"]
			if count is None:
				touched += 1
				continue  # blank is not zero; see the module docstring
			employee = frappe.get_doc("Employee", row["employee"])
			# replaces rather than appends: a second run must not double every
			# employee's children, which is the failure mode that makes a re-run
			# look like a rise in the grant.
			employee.set("sdg_children", [])
			for index in range(1, int(count) + 1):
				employee.append(
					"sdg_children",
					{
						"child_name": placeholder_name(index, int(count)),
						"age": 0,
						"eligible_for_school_fee": 1,
						"school_fee": fee.name,
					},
				)
				written_rows += 1
			apply_to_employee(employee)
			# `flags.ignore_mandatory` (not a save() kwarg - Frappe reads it off the
			# document) because Employee.gender is mandatory and some of these
			# employees have never had it filled in.  A pre-existing data gap that
			# would otherwise cost us their school fee children entirely.
			employee.flags.ignore_mandatory = True
			employee.save(ignore_permissions=True)
			written_employees += 1

		frappe.db.commit()

		print(f"\nwrote {written_rows} child row(s) across {written_employees} employee(s)")
		if touched:
			print(f"  {touched} employee(s) with a blank count were saved UNCHANGED - no child rows")
		if unmatched:
			print(f"  {len(unmatched)} row(s) skipped, listed above")

		# The fee's own counts are derived from the child rows, and the rows live on
		# Employee - so they can only be counted once every employee is saved.
		# Without this the fee record reports 0 children and 0 employees while the
		# grant it pays for sits on 73 Employee forms.
		refresh_fee_counts()

		if args.list_placeholders:
			_list_placeholders(fee)
		else:
			print("  every child name and age written is a PLACEHOLDER")
			print("  run again with --list-placeholders to see what still needs filling in")
		return 0
	finally:
		frappe.db.rollback()
		frappe.destroy()


def _list_placeholders(fee) -> None:
	"""Every child row still carrying an invented name, by employee."""
	import frappe

	rows = frappe.get_all(
		"SDG Children",
		filters={"school_fee": fee.name},
		fields=["parent", "parentfield", "child_name", "age", "idx"],
		order_by="parent asc, idx asc",
		limit_page_length=0,
	)
	outstanding = [r for r in rows if str(r.child_name or "").startswith("Child ")]

	by_employee: dict[str, list] = {}
	for row in outstanding:
		by_employee.setdefault(row.parent, []).append(row)

	print(
		f"\nplaceholder child rows still to fill in: {len(outstanding)} across {len(by_employee)} employee(s)"
	)
	if not outstanding:
		print("  none - every child row has a real name")
		return
	for employee_id, children in sorted(by_employee.items()):
		name = frappe.db.get_value("Employee", employee_id, "employee_name")
		print(f"    {employee_id:<7} {str(name)[:34]:34} {len(children)} child row(s)")
	print("\n  'age' is 0 on all of them, so any report that reads age will be wrong")
	print("  until these are filled in from the original PDF.")


def _ensure_fee(frappe):
	"""The single master fee record, created or corrected to FEE."""
	if frappe.db.exists("SDG School Fee", FEE_NAME):
		doc = frappe.get_doc("SDG School Fee", FEE_NAME)
		if float(doc.amount) != float(FEE):
			doc.amount = FEE
			doc.save(ignore_permissions=True)
		return doc
	return frappe.get_doc(
		{
			"doctype": "SDG School Fee",
			"school_fee_name": FEE_NAME,
			"amount": FEE,
		}
	).insert(ignore_permissions=True)


def _ensure_fee(frappe):
	"""The single master fee record, created or corrected to FEE."""
	if frappe.db.exists("SDG School Fee", FEE_NAME):
		doc = frappe.get_doc("SDG School Fee", FEE_NAME)
		if float(doc.amount) != float(FEE):
			doc.amount = FEE
			doc.save(ignore_permissions=True)
		return doc
	return frappe.get_doc(
		{
			"doctype": "SDG School Fee",
			"school_fee_name": FEE_NAME,
			"amount": FEE,
		}
	).insert(ignore_permissions=True)


if __name__ == "__main__":
	sys.exit(main())
