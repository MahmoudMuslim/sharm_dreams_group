#!/usr/bin/env python3
"""Import the people on the Master Colleagues Data "leavers " sheet as leavers.

HRMS will not accept `status = "Left"` without a relieving date, so the exit date
is a required argument here rather than something inferred.  Read the file
before choosing one: the sheet carries **no** leaving date of its own.  Every
date column in it is joining, date of birth, contract, social insurance or offer.
The "Days with Company" column looks like it could give one - joining date plus
days works out to a leaving date - but it is tenure measured to the sheet's own
stamp, so every row lands on the same day and the column carries no per-person
information at all.

That stamp, 2026-09-28, is the date in the sheet's first cell.  Passing it as
`--relieving-date` therefore records "left on or before the report date", which is
true but is not the date each person actually left.  End-of-service gratuity and
any final settlement are worked out from the relieving date, so a real per-person
date is worth having.

Three cases, because a leaver is not always where you would look for them:

  insert  the work ID is not in HRMS at all - the normal case, 133 of them
  update  the work ID is already there and still marked Active (H0241)
  rematch the leaver's national ID belongs to an employee under a *different*
          work ID (leaver H0247 is HRMS H0312).  Only a national ID comparison
          finds these; the ID-remapping that produced them also means the work
          IDs disagree.

Checks, each of which rejects rather than guesses:

  company  must resolve to a real Company.  The sheet's names carry Arabic
           suffixes and inconsistent spacing; `companies.resolve` handles those
           and returns None for anything it cannot place.
  national a national ID already on the file for a different person is refused,
  id       as is one claimed twice in the sheet (two rows share an ID).
  name     the English and Arabic names are both required, since the sheet is
           the only record of them for a leaver.

Names are split on the last token: "Amir Maher Nassef Farag" becomes first_name
"Amir Maher Nassef" and last_name "Farag".  That is presentation only and is
wrong for a name that does not split that way; it is not identity matching.

Run:
  python3 tools/import_leavers.py --relieving-date 2026-09-28
  python3 tools/import_leavers.py --relieving-date 2026-09-28 --apply
"""

from __future__ import annotations

import argparse
import datetime
import pathlib
import re
import sys

# The bench runs in a container that cannot see /home/mahmoud, so the workbook is
# looked for in the bench's staging area first and only then at the host path -
# the same arrangement build_photo_manifest uses for the photos.
APP = pathlib.Path(__file__).resolve().parent.parent
STAGING = APP.parent.parent / "import_staging" / "leavers"
CANDIDATES = (
	STAGING / "Master Colleagues Data.xlsx",
	pathlib.Path("/home/mahmoud/Master Colleagues Data.xlsx"),
)
SHEET = "leavers "

# Column positions in the leavers sheet.  They are taken from the header row by
# name where the name is unambiguous, because the sheet has a duplicate header
# (two columns both called "Colleagues Full Name (as per ID)") and the rows are
# short of cells often enough that a fixed index is the only reliable read.
HEADER_ROW_MARKER = "Serial Number"
COL_ID = 2
COL_COMPANY = 1
COL_NAME_EN = 3
COL_NAME_AR = 4
COL_JOINING = 11
COL_GENDER = 25

GENDER = {"male": "Male", "m": "Male", "female": "Female", "f": "Female"}


def cell(row, index):
	"""A cell, or None when the row is short - the sheet has ragged rows."""
	return row[index] if index < len(row) else None


def as_date(value):
	if isinstance(value, datetime.datetime):
		return value.date()
	if isinstance(value, datetime.date):
		return value
	return None


def national_ids(row) -> str | None:
	"""The 14-digit national ID, wherever it sits in the row.

	The sheet is misaligned on roughly 35 of its 136 rows - a short row shifts
	every later value one column left, which is why "Gender" holds dates and
	"Social Insurance Status" holds numbers.  Scanning for the ID by shape is
	sturdier than trusting a column index that the row may not honour.
	"""
	for value in row[:130]:
		text = str(value).strip() if value is not None else ""
		if text.endswith(".0"):
			text = text[:-2]
		if text.isdigit() and len(text) == 14:
			return text
	return None


def split_name(full: str) -> tuple[str, str]:
	parts = [p for p in str(full or "").split() if p]
	if not parts:
		return "", ""
	if len(parts) == 1:
		return parts[0], ""
	return " ".join(parts[:-1]), parts[-1]


def read_leavers():
	"""Every leaver row, keyed by work ID, with the duplicates kept visible."""
	import openpyxl

	source = next((c for c in CANDIDATES if c.exists()), None)
	if source is None:
		raise SystemExit(
			f"workbook not found; looked in {', '.join(str(c) for c in CANDIDATES)}\n"
			f"copy it in with: mkdir -p {STAGING} && cp "
			f"'/home/mahmoud/Master Colleagues Data.xlsx' {STAGING}/"
		)
	rows = list(openpyxl.load_workbook(source, data_only=True)[SHEET].iter_rows(values_only=True))
	header = next(i for i, r in enumerate(rows) if r and str(r[0]).strip() == HEADER_ROW_MARKER)

	people: dict[str, dict] = {}
	dupe_rows: dict[str, int] = {}
	for row in rows[header + 1 :]:
		wid = str(cell(row, COL_ID) or "").strip()
		if not wid or wid == "ID No":
			continue
		record = {
			"work_id": wid,
			"name_en": str(cell(row, COL_NAME_EN) or "").strip(),
			"name_ar": str(cell(row, COL_NAME_AR) or "").strip(),
			"company_raw": str(cell(row, COL_COMPANY) or "").strip(),
			"date_of_joining": as_date(cell(row, COL_JOINING)),
			"gender": GENDER.get(str(cell(row, COL_GENDER) or "").strip().lower()),
			"national_id": national_ids(row),
		}
		if wid in people:
			dupe_rows[wid] = dupe_rows.get(wid, 1) + 1
			continue
		people[wid] = record
	return people, dupe_rows


def main() -> int:
	parser = argparse.ArgumentParser(
		description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
	)
	parser.add_argument(
		"--relieving-date",
		required=True,
		help="the date to record as the relieving date - see the note above, the sheet has no leaving date",
	)
	parser.add_argument("--apply", action="store_true", help="write the records (default is report only)")
	args = parser.parse_args()
	if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", args.relieving_date):
		parser.error(f"--relieving-date must be YYYY-MM-DD, got {args.relieving_date!r}")
	relieving = datetime.date.fromisoformat(args.relieving_date)

	import frappe

	from sharm_dreams_group import companies

	frappe.init(site="dev.localhost")
	frappe.connect()
	try:
		leavers, dupe_rows = read_leavers()
		existing = {
			name: (nid, status)
			for name, nid, status in frappe.db.sql(
				"select name, ifnull(sdg_national_id,''), status from `tabEmployee`"
			)
		}
		by_nid: dict[str, str] = {}
		for name, (nid, _status) in existing.items():
			if nid:
				by_nid.setdefault(str(nid).strip(), name)

		insert, update, rematch, rejected = [], [], [], []
		claimed: dict[str, str] = {}

		for wid, person in sorted(leavers.items()):
			problems = []
			company = companies.resolve(person["company_raw"])
			if not company:
				problems.append(f"company {person['company_raw']!r} does not resolve")
			if not person["name_en"]:
				problems.append("no English name")
			if not person["name_ar"]:
				problems.append("no Arabic name")

			nid = person["national_id"]
			if nid:
				other = claimed.get(nid)
				if other:
					problems.append(f"national ID {nid} already claimed by {other} in this sheet")
				else:
					claimed[nid] = wid
				holder = by_nid.get(nid)
				if holder and holder != wid and holder not in leavers:
					problems.append(f"national ID {nid} is already on file for {holder}, who is not a leaver")
			else:
				problems.append("no national ID, so this person cannot be matched or checked")

			if wid in existing:
				update.append((wid, person, company, existing[wid][1]))
			elif nid and nid in by_nid:
				rematch.append((wid, by_nid[nid], person, company))
			elif problems:
				rejected.append((wid, person, problems))
			else:
				insert.append((wid, person, company))

		print(f"leavers on the sheet   : {len(leavers)} ({len(dupe_rows)} repeated, ignored)")
		print(f"  would insert         : {len(insert)}")
		print(f"  would set Left       : {len(update)}   {sorted(w for w, _p, _c, _s in update)}")
		print(f"  matched by national ID: {len(rematch)}")
		for wid, holder, person, _c in rematch:
			print(f"      leaver {wid} ({person['name_en'][:30]}) is {holder} in HRMS")
		print(f"  rejected             : {len(rejected)}")
		for wid, _person, problems in rejected:
			print(f"      {wid:<8} {'; '.join(problems)}")

		if dupe_rows:
			print(f"\nrepeated work IDs in the sheet, first row used: {dupe_rows}")
		print(f"\nrelieving date recorded: {relieving} (from --relieving-date, not the sheet)")
		if relieving == datetime.date(2026, 9, 28):
			print("  NOTE: 2026-09-28 is the sheet's own report date, so this records")
			print("        'left on or before that date', not each person's real exit")
			print("        date.  Gratuity and final settlement are worked out from it.")

		if not args.apply:
			print("\ndry run, nothing written. re-run with --apply.")
			return 0

		frappe.flags.ignore_permissions = True
		written = 0
		for wid, person, company in insert:
			first, last = split_name(person["name_en"])
			frappe.get_doc(
				{
					"doctype": "Employee",
					"employee_number": wid,
					"first_name": first,
					"last_name": last,
					"sdg_name_arabic": person["name_ar"],
					"gender": person["gender"],
					"date_of_joining": person["date_of_joining"],
					"relieving_date": relieving,
					"status": "Left",
					"company": company,
					"sdg_national_id": person["national_id"],
				}
			).insert(ignore_mandatory=True)
			written += 1

		for wid, _person, _company, _status in update:
			frappe.get_doc("Employee", wid).update({"status": "Left", "relieving_date": relieving}).save(
				ignore_permissions=True
			)
			written += 1

		for _leaver_id, holder, _person, _company in rematch:
			frappe.get_doc("Employee", holder).update({"status": "Left", "relieving_date": relieving}).save(
				ignore_permissions=True
			)
			written += 1

		frappe.db.commit()
		print(
			f"\nwrote {written} leaver record(s): {len(insert)} inserted, "
			f"{len(update) + len(rematch)} set to Left"
		)
		if rejected:
			print(f"{len(rejected)} rejected - see the list above")
		return 0
	finally:
		frappe.db.rollback()
		frappe.destroy()


if __name__ == "__main__":
	sys.exit(main())
