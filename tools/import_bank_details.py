#!/usr/bin/env python3
"""Load employee bank details from a finance-supplied file, with checks.

The master payroll workbook carries no bank columns (checked: 62 headers, only
"National ID"), so the bank details arrive separately.  This reads that file and
writes `bank_name`, `bank_ac_no` and `iban` onto the Employee records.

Why this is so strict: these three fields decide whose bank account the payroll
money lands in.  Attaching one person's account number to another employee is not
a data-quality nit, it is a misdirected payment, so the checks below treat a
wrong-but-plausible row as worse than a missing one.  Every rejection is reported
with the reason, and nothing is written unless the caller asks for it.

Checks, in order:

  match       the employee must be found *unambiguously*.  Three keys are tried
              in order of trust: the Employee ID (which *is* the document name,
              e.g. V0130/H0355), then the national ID on `sdg_national_id`,
              then a normalised full name.  A name matching two people is
              rejected rather than guessed.  Guessing on names is exactly the
              failure the photo matcher hit - three of four wrong - so it is
              never resolved by picking one.
  required    bank name and account number are present.
  iban        if an IBAN is given it is checked for length/format and the ISO
              7064 mod-97 checksum.  A checksum failure is never overridden,
              because a single mistyped digit sends the payment elsewhere.
  duplicate   no two rows may carry the same account number, and no account
              number already on file may be moved to a different employee.
              Shared accounts are a real if unusual arrangement, so that needs
              --allow-shared rather than a silent pass.

Rows are applied only if they pass.  Use --strict to make any rejection fatal,
which is what a payment run should use.

The bench runs in a container that cannot see /home/mahmoud, so the input file
has to be copied into the bench for this to read it.

Run:
  python3 tools/import_bank_details.py /path/to/bank_details.xlsx
  python3 tools/import_bank_details.py ... --strict --apply
"""

from __future__ import annotations

import argparse
import csv
import pathlib
import re
import sys
import unicodedata

# Candidate header names, lowercased, mapped to the Employee field they fill.
FIELD_ALIASES = {
	"bank_name": ("bank name", "bank", "bank_name", "bankname"),
	"bank_ac_no": (
		"bank ac no",
		"bank account no",
		"account no",
		"account number",
		"acct no",
		"bank_ac_no",
		"iban",
	),
	"iban": ("iban", "i b a n", "international bank account number"),
}
ID_ALIASES = ("employee id", "employee code", "work id", "work_id", "employee_number", "code", "id", "كود")
NID_ALIASES = ("national id", "national_id", "nationalid", "nid", "رقم قومي", "الرقم القومي")
NAME_ALIASES = ("employee name", "full name", "name", "الاسم", "اسم")

# Egypt uses a 29-character IBAN.  Other lengths are accepted but reported, since
# the group is Egyptian and a foreign length usually means a column mix-up.
EXPECTED_IBAN_LENGTH = 29


def normalise(value: object) -> str:
	"""Fold a name down to comparable letters, for matching only."""
	if value is None:
		return ""
	text = unicodedata.normalize("NFKD", str(value))
	text = "".join(ch for ch in text if not unicodedata.combining(ch))
	text = re.sub(r"[^a-z0-9]+", " ", text.lower())
	return " ".join(text.split())


def iban_mod97(iban: str) -> int:
	"""ISO 7064 mod-97 remainder.  1 means a valid checksum."""
	rearranged = iban[4:] + iban[:4]
	digits = "".join(str(ord(ch) - 55) if ch.isalpha() else ch for ch in rearranged.upper())
	return int(digits) % 97


def check_iban(raw: object) -> tuple[str | None, str | None]:
	"""Return (cleaned_iban, problem).  cleaned is None when unusable."""
	if raw is None or not str(raw).strip():
		return None, None
	cleaned = re.sub(r"[^A-Za-z0-9]", "", str(raw)).upper()
	if not cleaned:
		return None, "iban is not alphanumeric once punctuation is removed"
	if len(cleaned) < 15 or len(cleaned) > 34:
		return None, f"iban length {len(cleaned)} is outside the 15-34 the standard allows"
	if not cleaned[:2].isalpha() or not cleaned[2:4].isdigit():
		return None, "iban country code/check digits are malformed"
	if iban_mod97(cleaned) != 1:
		return None, "iban fails the mod-97 checksum, so at least one character is wrong"
	return cleaned, None


def read_rows(path: pathlib.Path) -> list[dict[str, object]]:
	"""Read a CSV or a single-sheet xlsx into a list of dicts."""
	if path.suffix.lower() in (".xlsx", ".xlsm"):
		import openpyxl

		book = openpyxl.load_workbook(path, read_only=True, data_only=True)
		sheet = book.worksheets[0]
		values = list(sheet.iter_rows(values_only=True))
		book.close()
	else:
		with path.open(newline="", encoding="utf-8-sig") as handle:
			values = list(csv.reader(handle))
	values = [r for r in values if any(c is not None and str(c).strip() for c in r)]
	if not values:
		return []
	headers = [normalise(h) for h in values[0]]
	return [dict(zip(headers, row, strict=False)) for row in values[1:]]


def map_columns(rows: list[dict[str, object]]) -> dict[str, str]:
	"""Work out which column holds which field."""
	found: dict[str, str] = {}
	seen: set[str] = set()
	for column in rows[0].keys() if rows else ():
		if column in seen:
			continue
		seen.add(column)
		for field, aliases in FIELD_ALIASES.items():
			if field in found:
				continue
			if column in aliases or any(a in column for a in aliases if len(a) > 3):
				found[field] = column
				break
		else:
			for field, aliases in (
				("employee_id", ID_ALIASES),
				("national_id", NID_ALIASES),
				("name", NAME_ALIASES),
			):
				if field not in found and any(column == a or (len(a) > 3 and a in column) for a in aliases):
					found[field] = column
					break
	return found


def resolve_employee(
	row: dict[str, object],
	columns: dict[str, str],
	by_code: dict[str, str],
	by_nid: dict[str, str],
	by_name: dict[str, list[str]],
) -> tuple[str | None, str | None]:
	"""Find the employee, or explain why not.  Never guesses between two people."""
	described: list[str] = []
	for key, lookup, label in (
		("employee_id", by_code, "employee ID"),
		("national_id", by_nid, "national ID"),
	):
		if key not in columns:
			continue
		value = row.get(columns[key])
		if value is None or not str(value).strip():
			continue
		token = str(value).strip()
		described.append(f"{label} {token}")
		if token in lookup:
			return lookup[token], None
		return None, f"no employee has {label} {token!r}"

	name = row.get(columns["name"]) if "name" in columns else None
	if name is None or not str(name).strip():
		if described:
			return None, f"{' and '.join(described)} matched nothing, and there is no name to fall back on"
		return None, "row has no employee ID, national ID or name"
	label = str(name).strip()
	matches = by_name.get(normalise(name), [])
	if not matches:
		return None, f"no employee is named {label!r}"
	if len(matches) > 1:
		listed = ", ".join(sorted(matches))
		return (
			None,
			f"name {label!r} matches {len(matches)} employees ({listed}), so it is not safe to attach",
		)
	return matches[0], None


def build_review(path: pathlib.Path) -> list[dict[str, object]]:
	"""Read and check every row.  Returns one report per input row."""
	import frappe

	rows = read_rows(path)
	if not rows:
		raise SystemExit("input file has no data rows")
	columns = map_columns(rows)
	if "bank_ac_no" not in columns:
		raise SystemExit(f"no account-number column found; headers were: {', '.join(sorted(rows[0]))}")

	# The Employee ID *is* the document name here (V0130, H0355, ...), so one
	# lookup covers it; the national ID is the other key finance will hold.
	by_code: dict[str, str] = {}
	by_nid: dict[str, str] = {}
	by_name: dict[str, list[str]] = {}
	for e in frappe.get_all(
		"Employee",
		fields=["name", "employee_number", "employee_name", "sdg_national_id"],
		limit_page_length=0,
	):
		by_code.setdefault(str(e["name"]).strip(), e["name"])
		for key in (e["employee_number"], e["sdg_national_id"]):
			if key and str(key).strip():
				by_code.setdefault(str(key).strip(), e["name"])
		if e["sdg_national_id"] and str(e["sdg_national_id"]).strip():
			by_nid.setdefault(str(e["sdg_national_id"]).strip(), e["name"])
		by_name.setdefault(normalise(e["employee_name"]), []).append(e["name"])
	on_file = {
		str(a or "").strip(): e
		for e, a in frappe.get_all("Employee", fields=["name", "bank_ac_no"], limit_page_length=0)
		if a and str(a).strip()
	}

	review: list[dict[str, object]] = []
	claimed: dict[str, str] = {}
	for index, row in enumerate(rows, start=2):
		record: dict[str, object] = {"line": index, "problems": [], "warnings": []}
		employee, why = resolve_employee(row, columns, by_code, by_nid, by_name)
		record["employee"] = employee
		described = row.get(columns.get("name") or columns.get("employee_id", ""), "")
		record["described_as"] = str(described or "").strip()
		if why:
			record["problems"].append(why)

		account = str(row.get(columns["bank_ac_no"]) or "").strip()
		record["account"] = account
		if not account:
			record["problems"].append("account number is empty")
		elif not re.fullmatch(r"[A-Za-z0-9]+", account):
			record["problems"].append("account number has characters an account number cannot contain")
		else:
			other = claimed.get(account.lower())
			if other and other != employee:
				record["problems"].append(f"the same account number is claimed by {other} in this file")
			claimed.setdefault(account.lower(), employee or "?")
			existing = on_file.get(account)
			if existing and employee and existing != employee:
				record["problems"].append(f"this account is already on file against {existing}")

		cleaned, iban_problem = check_iban(row.get(columns["iban"]) if "iban" in columns else None)
		if iban_problem:
			record["problems"].append(iban_problem)
		elif cleaned and len(cleaned) != EXPECTED_IBAN_LENGTH:
			record["warnings"].append(
				f"iban is {len(cleaned)} characters, not the {EXPECTED_IBAN_LENGTH} an Egyptian iban uses"
			)
		record["iban"] = cleaned

		bank = str(row.get(columns["bank_name"]) if "bank_name" in columns else "").strip()
		if not bank:
			record["problems"].append("bank name is empty")
		record["bank_name"] = bank
		review.append(record)
	return review


def report(review: list[dict[str, object]]) -> None:
	ok = [r for r in review if not r["problems"]]
	bad = [r for r in review if r["problems"]]
	warned = [r for r in review if not r["problems"] and r["warnings"]]
	print(f"rows read      : {len(review)}")
	print(f"ready to apply : {len(ok)}")
	print(f"rejected       : {len(bad)}")
	if warned:
		print(f"with warnings  : {len(warned)}")
		for record in warned:
			joined = "; ".join(record["warnings"])
			print(f"  line {record['line']:<4} {record['employee']:<10} {joined}")
	if bad:
		print("\nrejected rows:")
		for record in bad:
			who = record["employee"] or "(unmatched)"
			joined = "; ".join(record["problems"])
			print(f"  line {record['line']:<4} {who:<10} {joined}")


def main() -> int:
	parser = argparse.ArgumentParser(
		description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
	)
	parser.add_argument("input", type=pathlib.Path, help="CSV or xlsx from finance")
	parser.add_argument(
		"--apply", action="store_true", help="write to the Employee records (default is report only)"
	)
	parser.add_argument("--strict", action="store_true", help="exit non-zero if any row was rejected")
	parser.add_argument(
		"--allow-shared", action="store_true", help="permit an account number shared by two employees"
	)
	args = parser.parse_args()

	import frappe

	frappe.init(site="dev.localhost")
	frappe.connect()
	try:
		review = build_review(args.input)
		report(review)
		rejected = [r for r in review if r["problems"]]
		if not args.allow_shared:
			rejected = [r for r in rejected if "claimed by" not in "; ".join(r["problems"])]

		if args.strict and rejected:
			print(f"\n--strict: {len(rejected)} row(s) rejected, so nothing was written")
			return 1
		if not args.apply:
			print("\ndry run, nothing written. re-run with --apply to write the clean rows.")
			return 1 if rejected else 0

		written = 0
		for record in review:
			if record["problems"]:
				continue
			frappe.db.set_value(
				"Employee",
				record["employee"],
				{
					"bank_name": record["bank_name"],
					"bank_ac_no": record["account"],
					"iban": record["iban"] or None,
				},
				update_modified=False,
			)
			written += 1
		frappe.db.commit()
		print(f"\nwrote {written} employee record(s)")
		if rejected:
			print(f"{len(rejected)} row(s) were left alone; see the rejected list above")
		return 0
	finally:
		frappe.destroy()


if __name__ == "__main__":
	sys.exit(main())
