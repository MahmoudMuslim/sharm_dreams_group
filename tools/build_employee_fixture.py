#!/usr/bin/env python3
"""Build the employee import fixture from the two source workbooks.

Reads, and never writes:
    ~/Master Colleagues Data.xlsx                    (employee master, 178 people)
    ~/Master Payroll (corrected tax + PoD flag).xlsx (payroll, 170 people / 207 lines)

Writes:
    seed_data/employees.json

Frappe-free on purpose.  Company names and designations are resolved on the app
side, where the live Company / Designation lists are, so the two never drift
apart: this file carries the raw source string and the app maps it.

Run:  python3 tools/build_employee_fixture.py
"""

from __future__ import annotations

import json
import pathlib
import re
import sys
import warnings
from collections import defaultdict
from datetime import date, datetime

import openpyxl

warnings.filterwarnings("ignore", category=UserWarning, module="openpyxl")

ROOT = pathlib.Path(__file__).resolve().parent.parent
MASTER_XLSX = pathlib.Path("/home/mahmoud/Master Colleagues Data.xlsx")
PAYROLL_XLSX = pathlib.Path("/home/mahmoud/Master Payroll (corrected tax + PoD flag).xlsx")
OUT = ROOT / "seed_data" / "employees.json"

# The payroll sheet's last data row; everything below is a footer.  Row 210 is a
# totals row whose "National ID" is really the highest serial number, so the
# range has to be bounded rather than read to the end of the sheet.
PAYROLL_FIRST_ROW = 3
PAYROLL_LAST_ROW = 209

# --- master column indices (header is on row 3) ----------------------------
M = {
	"company": 1,
	"id_no": 2,
	"name_en": 3,
	"name_ar": 4,
	"starting_position": 5,
	"position_ar": 6,
	"position_en": 7,
	"department": 9,
	"branch": 10,
	"sub_department": 11,
	"job_profile": 8,
	"joining_date": 12,
	"grade": 20,
	"employment_category": 21,
	"contract_type": 22,
	"contract_date": 23,
	"dob": 25,
	"gender": 27,
	"religion": 28,
	"marital_status": 29,
	"dependants": 30,
	"military_status": 31,
	"military_document": 32,
	"personal_email": 36,
	"business_email": 37,
	"home_address": 38,
	"governorate": 39,
	"emergency_phone": 40,
	"tel": 41,
	"national_id": 73,
	"national_id_expiry": 74,
	"si_status": 76,
	"si_number": 77,
	"si_date": 78,
	"offer_status": 79,
	"status_date": 80,
	# "6 PIC" - whether a photo is on file.  Also the pool the photo matcher
	# narrows to, which is what makes name matching tractable.
	"six_pic": 95,
}

# The master's "EMPLOYEE FILE INDEX" block, columns 92..109.  Kept as a list of
# (source header, canonical label) so the fieldnames stay stable if a later
# workbook renames a column, and so a stray value is reportable rather than
# silently folded into a status.
FILE_INDEX = [
	("HRA", "HRA"),
	("LOI", "LOI"),
	("ID", "ID"),
	("6 PIC", "6 PIC"),
	("MILITARY", "Military"),
	("MEDICAL 111", "Medical 111"),
	("SOCIAL S1", "Social S1"),
	("CRIMINAL RECORD", "Criminal Record"),
	("EDUCATION CERTIFICATE", "Education Certificate"),
	("EMPLOYMENT PERMT", "Employment Permit"),
	("Security check", "Security Check"),
	("BANK DETAILS", "Bank Details"),
	("EMPLOYMENT OF RELATIVES", "Employment of Relatives"),
	("BENEFICIARY", "Beneficiary"),
	("CODE OF BUSINESS CONDUCT", "Code of Business Conduct"),
	("UPDATED CV / INTERVIEW PAPERS", "Updated CV / Interview Papers"),
	("JOB DESCRIPTION", "Job Description"),
	("Refernce Check", "Reference Check"),
]

# The master writes Y, N, NA, "N/A", and occasionally a lower-case "y".  "Copy"
# on EDUCATION CERTIFICATE is a stray note on one row, not a status.
FILE_STATUS = {"y": "Held", "n": "Missing", "na": "Not Applicable", "n/a": "Not Applicable"}

# The block starts at column 92 (HRA) and runs to 109 (Refernce Check).
FILE_INDEX_FIRST_COL = 92

# --- payroll column indices (header is split across rows 1 and 2) ----------
P = {
	"work_id": 1,
	"name_en": 2,
	"name_ar": 3,
	"company": 4,
	"delegated_company": 5,
	"position": 6,
	"department": 7,
	"location": 8,
	"national_id": 9,
	"si_number": 10,
	"joining_date": 11,
	"dob": 13,
	"gross_salary": 15,
	"net_salary": 16,
	"fixed_incentives": 18,
	"overtime": 19,
	"rest_allowance": 20,
	"housing_transport": 21,
	"exceptional_bonus": 22,
	"total_earning": 27,
	"penalties": 29,
	"absenteeism": 31,
	"pre_tax_deductions": 32,
	"absent_days": 30,
	"penalty_days": 28,
	"life_insurance": 33,
	"medical_insurance": 34,
	"loan": 48,
	"phone": 49,
	"family_medical": 50,
	"refunded": 51,
	"account_number": 59,
	"payment_method": 60,
	"pod": 61,
}

# Normalised on the way in, so the stored values match the Select options.
RELIGION = {"muslim": "Muslim", "christian": "Christian", "christians": "Christian"}
MILITARY_STATUS = {
	"completed": "Completed",
	"exempted": "Exempted",
	"exempt": "Exempted",
	"not required": "Not Required",
	"notrequired": "Not Required",
	"postponed": "Postponed",
}
MILITARY_DOCUMENT = {
	"missing": "Missing",
	"on file": "On File",
	"out and not returned": "Out and not Returned",
	"out and not returneded": "Out and not Returned",
}
OFFER_STATUS = {
	"on board": "On board",
	"onboard": "On board",
	"off board": "Off board",
	"offboard": "Off board",
}
GENDER = {"male": "Male", "m": "Male", "female": "Female", "f": "Female"}
# Social Insurance Status is a Select of exactly these two values.  The master
# writes the column in mixed case ("uninsured" on 31 rows, "insured" on 1), so
# case is normalised here rather than left to fail Select validation on import.
SI_STATUS = {"insured": "Insured", "uninsured": "Uninsured"}


# ---------------------------------------------------------------------------
# value cleaning
# ---------------------------------------------------------------------------
def cell(row, index):
	return row[index] if index < len(row) else None


def text(value) -> str:
	"""Collapse whitespace; empty for anything that carries no information."""
	if value is None:
		return ""
	if isinstance(value, str):
		clean = " ".join(value.split())
		return "" if clean.lower() in {"nan", "none", "-", "n/a"} else clean
	if isinstance(value, (int, float)):
		return str(int(value)) if float(value).is_integer() else str(value)
	return str(value)


def digits(value) -> str:
	"""National IDs arrive as ints, floats and strings; keep only digits."""
	if value is None:
		return ""
	if isinstance(value, bool):
		return ""
	if isinstance(value, (int, float)):
		return str(int(value)) if float(value).is_integer() else str(value)
	return re.sub(r"\D", "", str(value))


def nid_encoded_dob(nid: str) -> str:
	"""The birth date encoded in an Egyptian National ID, or "" if unreadable.

	Format: one century digit, two year digits, two month digits, two day digits
	- so `27309051401614` is 1973-09-05.  This is what makes the two source files
	checkable against each other: where they disagree about someone's National ID,
	the one that reproduces their date of birth is the correct one.

	H0117 and H0342 are both settled by this.  H0117's master value duplicates
	H0116's and encodes 1977-08-10, while his date of birth is 1967-08-29, which
	the payroll value reproduces.  H0342 is the reverse: payroll has him carrying
	H0343's ID, and the master's value encodes his own date of birth.
	"""
	if len(nid) < 7 or not nid[:7].isdigit():
		return ""
	century, year, month, day = nid[0], int(nid[1:3]), int(nid[3:5]), int(nid[5:7])
	try:
		return date((1900 if century in "12" else 2000) + year, month, day).isoformat()
	except ValueError:
		return ""


def dob_disagreements(people: list[dict]) -> list[dict]:
	"""Records whose National ID does not reproduce their date of birth.

	Reported, not fixed.  In most of these the ID is the plausible value and the
	date of birth was mistyped, but correcting someone's birth date is HR's call,
	not the importer's.
	"""
	out = []
	for record in people:
		dob = record.get("date_of_birth")
		if not dob:
			continue
		encoded = nid_encoded_dob(record["national_id"])
		if encoded and encoded != dob:
			out.append(
				{
					"employee_number": record["employee_number"],
					"national_id": record["national_id"],
					"date_of_birth": dob,
					"national_id_encodes": encoded,
				}
			)
	return out


def iso(value) -> str:
	"""ISO date, or empty.  Excel hands back datetimes and a few text dates."""
	if value in (None, ""):
		return ""
	if isinstance(value, datetime):
		return value.date().isoformat()
	if isinstance(value, date):
		return value.isoformat()
	raw = str(value).strip()
	if not raw:
		return ""
	for fmt in ("%d-%m-%Y", "%d/%m/%Y", "%Y-%m-%d", "%d-%m-%y", "%d.%m.%Y"):
		try:
			return datetime.strptime(raw, fmt).date().isoformat()
		except ValueError:
			continue
	return ""


def number(value):
	if value in (None, ""):
		return None
	if isinstance(value, (int, float)):
		return round(float(value), 2)
	raw = str(value).replace(",", "").strip()
	try:
		return round(float(raw), 2)
	except ValueError:
		return None


def truthy(value) -> bool:
	return text(value).strip().lower() in {"1", "yes", "y", "true", "x"}


def split_name(full: str) -> dict:
	"""first / middle / last from a single full-name cell.

	ERPNext derives `employee_name` from these three, so they have to be real
	parts rather than one lump: first word first, last word last, the rest as
	the middle name.  A single-word name becomes the first name, because leaving
	`last_name` empty makes the desk render "Sabry Abouel-Fottouh" oddly.
	"""
	parts = [p for p in text(full).split(" ") if p]
	if not parts:
		return {"first_name": "", "middle_name": "", "last_name": ""}
	if len(parts) == 1:
		return {"first_name": parts[0], "middle_name": "", "last_name": ""}
	return {"first_name": parts[0], "middle_name": " ".join(parts[1:-1]), "last_name": parts[-1]}


def pick(mapping: dict, value: str) -> str:
	return mapping.get(text(value).lower(), text(value))


# ---------------------------------------------------------------------------
# readers
# ---------------------------------------------------------------------------
def read_master() -> list[dict]:
	book = openpyxl.load_workbook(MASTER_XLSX, read_only=True, data_only=True)
	sheet = book["Master"]

	out = []
	for row in sheet.iter_rows(min_row=4, values_only=True):
		id_no = text(cell(row, M["id_no"]))
		name = text(cell(row, M["name_en"]))
		# Three rows carry a National ID but no name and no ID No: they are
		# continuation rows left over from a split, not people.
		if not id_no or not name:
			continue
		record = {key: text(cell(row, idx)) for key, idx in M.items()}
		record["id_no"] = id_no
		record["national_id"] = digits(record["national_id"])
		for key in ("joining_date", "contract_date", "dob", "national_id_expiry", "si_date", "status_date"):
			record[key] = iso(cell(row, M[key]))
		record["dependants"] = number(cell(row, M["dependants"])) or 0
		record["religion"] = pick(RELIGION, record["religion"])
		record["gender"] = pick(GENDER, record["gender"])
		record["military_status"] = pick(MILITARY_STATUS, record["military_status"])
		record["military_document"] = pick(MILITARY_DOCUMENT, record["military_document"])
		record["offer_status"] = pick(OFFER_STATUS, record["offer_status"])
		record["si_status"] = pick(SI_STATUS, record["si_status"])

		# EMPLOYEE FILE INDEX, columns 92..109 in order
		record["file_index"] = []
		for offset, (_header, label) in enumerate(FILE_INDEX):
			raw = text(cell(row, FILE_INDEX_FIRST_COL + offset))
			status = FILE_STATUS.get(raw.lower(), "")
			record["file_index"].append({"document_item": label, "status": status, "raw": raw})
		out.append(record)
	return out


def read_payroll() -> tuple[dict, dict]:
	"""Return ({national_id: first row}, {national_id: [all rows]})."""
	book = openpyxl.load_workbook(PAYROLL_XLSX, read_only=True, data_only=True)
	sheet = book["Master Payroll"]

	by_nid: dict[str, dict] = {}
	all_rows: dict[str, list] = defaultdict(list)
	for row in sheet.iter_rows(min_row=PAYROLL_FIRST_ROW, max_row=PAYROLL_LAST_ROW, values_only=True):
		nid = digits(cell(row, P["national_id"]))
		if not nid:
			continue
		record = {key: text(cell(row, idx)) for key, idx in P.items()}
		record["national_id"] = nid
		record["work_id"] = text(record["work_id"])
		for key in ("joining_date", "dob"):
			record[key] = iso(cell(row, P[key]))
		for key in (
			"gross_salary",
			"net_salary",
			"fixed_incentives",
			"overtime",
			"rest_allowance",
			"housing_transport",
			"exceptional_bonus",
			"life_insurance",
			"medical_insurance",
			"family_medical",
			"loan",
			# pre-tax deductions, read by the engine as `monthly_deductions`
			"penalties",
			"absenteeism",
			"pre_tax_deductions",
			"absent_days",
			"penalty_days",
		):
			record[key] = number(cell(row, P[key]))
		record["pod"] = truthy(cell(row, P["pod"]))
		all_rows[nid].append(record)
		by_nid.setdefault(nid, record)
	return by_nid, all_rows


# ---------------------------------------------------------------------------
# reconciliation
# ---------------------------------------------------------------------------
# Repairs for records whose dates contradict each other, so the build is not
# blocked.  Each entry states the evidence, and every repair is flagged on the
# record so HR can audit and undo it.  Employee.validate_dates rejects a joining
# date on or before the date of birth, so these cannot be imported as they stand.
#
# V0102: the master has the two dates transposed.  The payroll carries
#   DOB 1998-04-15 and joining 2025-04-01, which is exactly the reverse of the
#   master's values, and payroll's own Age of 28 agrees with 1998.  Both real
#   values come from payroll; nothing is invented.
# H0304: master-only, so the payroll has nothing.  The National ID encodes
#   1980-01-06, which is the only evidence for the date of birth; the master's
#   joining date of 2025-10-01 is then consistent.
# H0355: the payroll's own dates contradict each other.  Its DOB of 1999-04-16
#   is corroborated by its Age of 27, while its joining date of 1996-08-16 is a
#   copy of the birth date encoded in the National ID.  No source holds a real
#   joining date, so one is set from the date of birth.  This is the only
#   invented value in the whole import and it feeds tenure, which is display
#   only - the tax engine uses annual taxable income and a flat exemption.
DATE_REPAIRS: dict[str, dict] = {
	"V0102": {
		"date_of_birth": "1998-04-15",
		"date_of_joining": "2025-04-01",
		"reason": "master transposed the date of birth and the joining date; both taken from payroll",
	},
	"H0304": {
		"date_of_birth": "1980-01-06",
		"reason": "date of birth decoded from the National ID; the master's 2025 was not plausible",
	},
	"H0355": {
		"date_of_joining": "2017-04-16",
		"invented": True,
		"reason": (
			"payroll's joining date duplicates the birth date encoded in the National ID; "
			"no source holds a real joining date, so this is derived from the date of "
			"birth and needs HR to confirm"
		),
	},
}


def apply_date_repairs(people: list[dict]) -> list[str]:
	"""Correct the impossible date pairs, and record that it was done."""
	repaired = []
	for record in people:
		fix = DATE_REPAIRS.get(record["employee_number"])
		if not fix:
			continue
		changes = []
		for field in ("date_of_birth", "date_of_joining"):
			if field in fix:
				changes.append(f"{field} {record.get(field)} -> {fix[field]}")
				record[field] = fix[field]
		flag = "dates_invented" if fix.get("invented") else "dates_repaired"
		record.setdefault("flags", []).append(f"{flag}: {fix['reason']}")
		if fix.get("invented"):
			record["flags"].append("review_required")
		repaired.append(f"{record['employee_number']} ({'; '.join(changes)})")
	return repaired


# ---------------------------------------------------------------------------
# reconciliation
# ---------------------------------------------------------------------------
def repair_master_national_ids(master: list[dict], payroll_by_nid: dict) -> list[str]:
	"""Repair master National IDs that collide with another master row.

	H0116 and H0117 share `27708102102856` in the master.  Left alone, the
	duplicate makes the join ambiguous and H0117 comes out twice.

	This has to run before the join rather than after, because until the
	collision is gone `H0117` is both "master only" and "payroll only".
	"""
	counts = defaultdict(list)
	for record in master:
		if record["national_id"]:
			counts[record["national_id"]].append(record["id_no"])

	by_work_id: dict[str, dict] = {}
	for record in payroll_by_nid.values():
		by_work_id.setdefault(record["work_id"], record)

	corrected = []
	for nid, ids in counts.items():
		if len(ids) < 2:
			continue
		for id_no in ids:
			payroll_row = by_work_id.get(id_no)
			if not payroll_row or payroll_row["national_id"] == nid:
				continue
			for record in master:
				if record["id_no"] == id_no:
					record["national_id"] = payroll_row["national_id"]
					record["flags_pre"] = ["national_id_corrected_from_payroll"]
					corrected.append(id_no)
	return corrected


def settle_national_id_conflicts(master: list[dict], payroll_by_nid: dict) -> list[str]:
	"""Where master and payroll disagree, keep the ID that matches the birth date.

	An Egyptian National ID encodes its holder's date of birth, so the two files
	can be checked rather than ranked by preference.  Three people have a
	transposed or mistyped digit somewhere, and in each case exactly one of the
	two values reproduces the date of birth on the record, so the choice is
	determined by the data rather than by a rule of thumb.

	Applies to a collision caused either way: payroll may have written another
	employee's ID (H0342 carries H0343's), or the master may have (H0117 carried
	H0116's).
	"""
	by_work_id: dict[str, dict] = {}
	for record in payroll_by_nid.values():
		by_work_id.setdefault(record["work_id"], record)

	settled: list[str] = []
	for record in master:
		payroll_row = by_work_id.get(record["id_no"])
		if not payroll_row:
			continue
		master_nid = record["national_id"]
		payroll_nid = payroll_row["national_id"]
		if not master_nid or not payroll_nid or master_nid == payroll_nid:
			continue
		dob = record["dob"]
		master_ok = bool(dob) and nid_encoded_dob(master_nid) == dob
		payroll_ok = bool(dob) and nid_encoded_dob(payroll_nid) == dob
		if master_ok == payroll_ok:
			# nothing to decide on, or neither reproduces the birth date
			continue
		record["national_id"] = master_nid if master_ok else payroll_nid
		record["flags_pre"] = [
			f"national_id_conflict_resolved: master {master_nid} encodes "
			f"{nid_encoded_dob(master_nid) or 'an invalid date'}, payroll {payroll_nid} "
			f"encodes {nid_encoded_dob(payroll_nid) or 'an invalid date'}, date of birth {dob}"
		]
		settled.append(record["id_no"])
	return settled


def reconcile(master: list[dict], payroll_by_nid: dict) -> list[dict]:
	"""One record per person, joined on ID No first, then National ID.

	ID No is the primary key because it is the more reliable of the two.  Three
	people (H0261, H0317, V0102) have a transposed digit in the National ID in
	one file or the other, and keying on the National ID emitted each of them
	twice.  A small number go the other way - the master and payroll use different
	ID numbers for the same person - so the National ID is the fallback pass.

	Records come out in a stable order with provenance and flags, so the import
	report can explain every decision without re-reading the workbooks.
	"""
	by_id_no = {r["id_no"]: r for r in master}
	by_nid: dict[str, dict] = {}
	for record in master:
		by_nid.setdefault(record["national_id"], record)

	people: list[dict] = []
	done_master: set[str] = set()
	done_payroll: set[str] = set()

	def merge(m: dict, p: dict | None, flags: list[str], source: str) -> None:
		flags = list(flags) + list(m.get("flags_pre", []))
		# The master's name wins; payroll is only a fallback.  Recorded because
		# the two files spell some names differently.
		name = m["name_en"] if source == "both" else (m["name_en"] or p["name_en"])
		record = {
			"employee_number": m["id_no"] or p["work_id"],
			"master_id_no": m["id_no"],
			"payroll_work_id": p["work_id"] if p else "",
			# already settled by settle_national_id_conflicts() where the two
			# files disagreed; the master value is the corrected one if it moved
			"national_id": m["national_id"] or (p["national_id"] if p else ""),
			"master_national_id": m["national_id"],
			"payroll_national_id": p["national_id"] if p else "",
			"first_name": "",
			"middle_name": "",
			"last_name": "",
			"name_arabic": m["name_ar"] or (p["name_ar"] if p else ""),
			"gender": m["gender"],
			"date_of_birth": m["dob"] or (p["dob"] if p else ""),
			"date_of_joining": m["joining_date"] or (p["joining_date"] if p else ""),
			"company_source": m["company"] or (p["company"] if p else ""),
			"designation_source": m["position_en"] or (p["position"] if p else ""),
			"designation_arabic": m["position_ar"],
			"starting_designation_source": m["starting_position"],
			"department": m["department"] or (p["department"] if p else ""),
			"branch": m["branch"],
			"sub_department": m["sub_department"],
			"grade": m["grade"],
			"employment_category": m["employment_category"],
			"contract_type": m["contract_type"],
			"contract_date": m["contract_date"],
			"offer_status": m["offer_status"],
			"status_date": m["status_date"],
			"religion": m["religion"],
			"marital_status": m["marital_status"],
			"sdg_dependants_count": m["dependants"],
			"military_status": m["military_status"],
			"military_document": m["military_document"],
			"sdg_national_id_expiry": m["national_id_expiry"],
			"sdg_si_number": m["si_number"] or (p["si_number"] if p else ""),
			"sdg_si_status": m["si_status"],
			"sdg_si_date": m["si_date"],
			"personal_email": m["personal_email"],
			"company_email": m["business_email"],
			"current_address": m["home_address"],
			"sdg_governorate": m["governorate"],
			"cell_number": m["tel"] or (p["phone"] if p else ""),
			"emergency_phone_number": m["emergency_phone"],
			"sdg_is_person_of_determination": p["pod"] if p else False,
			"job_profile": m["job_profile"],
			"has_photo_on_file": m["six_pic"].upper() == "Y" if m["six_pic"] else False,
			"file_index": m["file_index"],
			"bank_account": p["account_number"] if p else "",
			"payment_method": p["payment_method"] if p else "",
			"source": source,
			"flags": flags,
		}
		record.update(split_name(name))
		people.append(record)

	# pass 1: same ID No in both files
	for work_id, p in sorted(payroll_by_nid.items()):
		work_id = p["work_id"]
		m = by_id_no.get(work_id)
		if not m:
			continue
		flags = []
		if m["national_id"] and m["national_id"] != p["national_id"]:
			flags.append(f"national_id_differs: master {m['national_id']} vs payroll {p['national_id']}")
		merge(m, p, flags, "both")
		done_master.add(m["id_no"])
		done_payroll.add(p["national_id"])

	# pass 2: same person, different ID No - matched on the National ID
	for nid, m in sorted(by_nid.items()):
		if m["id_no"] in done_master or not nid or nid not in payroll_by_nid:
			continue
		p = payroll_by_nid[nid]
		if p["national_id"] in done_payroll:
			continue
		merge(m, p, [f"id_differs: master {m['id_no']} vs payroll {p['work_id']}"], "both")
		done_master.add(m["id_no"])
		done_payroll.add(nid)

	# pass 3: master only
	for m in sorted(master, key=lambda r: r["id_no"]):
		if m["id_no"] in done_master:
			continue
		merge(m, None, ["no_payroll_record"], "master")

	# pass 4: payroll only
	blank = {key: "" for key in M}
	blank["dependants"] = 0
	# no master row, so no document checklist and no picture flag
	blank["file_index"] = []
	for nid, p in sorted(payroll_by_nid.items()):
		if nid in done_payroll:
			continue
		merge(blank, p, ["no_master_record", "review_required"], "payroll")

	# sanity: National IDs must be unique, or the join was not stable
	seen: dict[str, str] = {}
	collisions = []
	for record in people:
		nid = record["national_id"]
		if not nid:
			continue
		if nid in seen:
			collisions.append(f"{record['employee_number']} and {seen[nid]} share {nid}")
		seen[nid] = record["employee_number"]
	if collisions:
		for record in people:
			if any(record["employee_number"] in c for c in collisions):
				record["flags"].append("duplicate_national_id")
				record["flags"].append("review_required")
		print("CONFLICT duplicate National IDs: " + "; ".join(collisions), file=sys.stderr)

	# 6. master and payroll disagree, and neither value reproduces the date of
	#    birth, so the birth date cannot arbitrate.  The master's value is kept -
	#    it is the employee master, the HR record of truth - but the record is
	#    flagged, because the National ID is the join key to payroll and medical.
	for record in people:
		master_nid = record.get("master_national_id")
		payroll_nid = record.get("payroll_national_id")
		if not master_nid or not payroll_nid or master_nid == payroll_nid:
			continue
		if any("national_id_conflict_resolved" in f for f in record["flags"]):
			continue
		dob = record.get("date_of_birth")
		record["flags"].append(
			f"national_id_unverified: master {master_nid} vs payroll {payroll_nid} "
			f"(date of birth {dob or 'unknown'} matches neither, so the master's was kept)"
		)
		record["flags"].append("review_required")

	people.sort(key=lambda r: (r["source"] != "both", r["employee_number"]))
	return people


# ---------------------------------------------------------------------------
# extra payroll detail (multi-company lines)
# ---------------------------------------------------------------------------
def attach_payroll_lines(people: list[dict], all_rows: dict) -> None:
	"""Record how many payroll lines each person has, and what they contain.

	207 payroll lines belong to 170 people: some are booked in more than one group
	company.  The extra lines are what a later group-allocation step needs, so
	they are carried rather than discarded.

	Indexed by the *payroll* National ID, not the stored one.  Where the two files
	disagree and the master's value was kept, the payroll rows are still filed
	under payroll's value, and looking up the stored one would silently drop those
	people's pay entirely.
	"""
	claimed: set[str] = set()
	for record in people:
		key = record.get("payroll_national_id") or record["national_id"]
		if not key or key in claimed:
			record["payroll_line_count"] = 0
			record["payroll_lines"] = []
			continue
		claimed.add(key)
		rows = all_rows.get(key, [])
		record["payroll_line_count"] = len(rows)
		record["payroll_lines"] = [
			{
				"company": row["company"],
				"delegated_company": row["delegated_company"],
				# the first line is the statutory employment; the rest are cost
				# shares recharged to another group company and paid at zero net
				"is_delegated": bool(row["delegated_company"]),
				"gross_salary": row["gross_salary"],
				# the workbook's own Total Earning.  It is *not* the same as
				# Gross Salary: the sum of the earning components is compared
				# against this, and 206 of 207 rows reconcile exactly.
				"total_earning": row["total_earning"],
				"net_salary": row["net_salary"],
				# pre-tax: read by the engine as `monthly_deductions`
				"penalties": row["penalties"] or 0.0,
				"absenteeism": row["absenteeism"] or 0.0,
				"pre_tax_deductions": row["pre_tax_deductions"] or 0.0,
				"absent_days": row["absent_days"] or 0.0,
				"penalty_days": row["penalty_days"] or 0.0,
				# post-tax: HRMS subtracts total_loan_repayment after total_deduction
				"loan": row["loan"] or 0.0,
				"position": row["position"],
				"earnings": {
					"Basic": row["gross_salary"],
					"Fixed Monthly Incentives": row["fixed_incentives"] or 0.0,
					"Rest Allowance": row["rest_allowance"] or 0.0,
					"Housing & Transport": row["housing_transport"] or 0.0,
					"Exceptional Bonus": row["exceptional_bonus"] or 0.0,
				},
			}
			for row in rows
		]
		if len(rows) > 1:
			record["flags"].append("multiple_payroll_lines")


def main() -> int:
	master = read_master()
	payroll_by_nid, all_rows = read_payroll()
	corrected = repair_master_national_ids(master, payroll_by_nid)
	settled = settle_national_id_conflicts(master, payroll_by_nid)
	people = reconcile(master, payroll_by_nid)
	attach_payroll_lines(people, all_rows)
	repaired = apply_date_repairs(people)

	counts = defaultdict(int)
	for record in people:
		counts[record["source"]] += 1
	multi = sum(1 for r in people if r.get("payroll_line_count", 0) > 1)

	payload = {
		"generated": datetime.now().isoformat(timespec="seconds"),
		"sources": {
			"master": MASTER_XLSX.name,
			"payroll": PAYROLL_XLSX.name,
		},
		"summary": {
			"people": len(people),
			"from_both": counts["both"],
			"master_only": counts["master"],
			"payroll_only": counts["payroll"],
			"national_ids_corrected": sorted(corrected),
			"national_ids_conflict_resolved": sorted(settled),
			"date_of_birth_disagreements": dob_disagreements(people),
			"dates_repaired": repaired,
			"payroll_lines": sum(r.get("payroll_line_count", 0) for r in people),
			"people_with_multiple_lines": multi,
		},
		"employees": people,
	}

	OUT.parent.mkdir(parents=True, exist_ok=True)
	OUT.write_text(json.dumps(payload, indent=1, ensure_ascii=False) + "\n")

	print(f"wrote {OUT.relative_to(ROOT)}")
	print(f"  people            : {len(people)}")
	print(f"  matched on both   : {counts['both']}")
	print(f"  master only       : {counts['master']}")
	print(f"  payroll only      : {counts['payroll']}")
	print(f"  payroll lines     : {payload['summary']['payroll_lines']} across {multi} multi-company people")
	print(f"  dates repaired    : {len(repaired)}")
	for r in repaired:
		print(f"     {r}")
	unverified = [r for r in people if any("national_id_unverified" in f for f in r["flags"])]
	print(f"  NID unverified  : {[r['employee_number'] for r in unverified] or 'none'}")
	flagged = [r for r in people if r["flags"]]
	print(f"  records with flags: {len(flagged)}")
	print(f"  NIDs corrected     : {sorted(corrected) or 'none'}")
	print(f"  NID conflicts fixed: {sorted(settled) or 'none'}")
	dob = dob_disagreements(people)
	print(f"  NID/DOB mismatches : {len(dob)} (reported, not changed)")
	for row in dob:
		print(
			f"     {row['employee_number']:8} NID {row['national_id']} encodes "
			f"{row['national_id_encodes']}, record says {row['date_of_birth']}"
		)
	return 0


if __name__ == "__main__":
	raise SystemExit(main())
