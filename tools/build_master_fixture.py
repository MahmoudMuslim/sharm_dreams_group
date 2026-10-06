"""Turn the master workbook into a reference-data fixture for the seed.

Produces `seed_data/master_reference.json`, holding the three vocabularies and
the designation list.  Kept separate from `seed_data/seed.json` (the band tables)
because this one is rebuilt whenever the master changes and is not band data.

Run: python3 tools/build_master_fixture.py
"""

from __future__ import annotations

import collections
import json
import pathlib
import warnings

import openpyxl

warnings.filterwarnings("ignore")

SOURCE = pathlib.Path("/home/mahmoud/Master Colleagues Data.xlsx")
OUT = pathlib.Path(__file__).resolve().parent.parent / "seed_data" / "master_reference.json"

COL = {
	"company": 2,  # B
	"work_id": 3,  # C
	"name": 4,  # D
	"name_ar": 5,  # E
	"starting_position": 6,  # F
	"position_ar": 7,  # G
	"position_en": 8,  # H
	"job_profile": 9,  # I
	"department": 10,  # J
	"branch": 11,  # K
	"sub_department": 12,  # L
	"joining_date": 13,  # M
	"employment_category": 22,  # V
	"contract_type": 23,  # W
	"contract_date": 24,  # X
	"contract_expiry": 25,  # Y
	"date_of_birth": 26,  # Z
	"gender": 28,  # AB
	"religion": 29,  # AC
	"marital_status": 30,  # AD
	"dependants": 31,  # AE
	"military_status": 32,  # AF
	"education_level": 34,  # AH
	"graduation_year": 35,  # AI
	"university": 36,  # AJ
	"personal_email": 37,  # AK
	"business_email": 38,  # AL
	"home_address": 39,  # AM
	"governorate": 40,  # AN
	"next_of_kin": 41,  # AO
	"telephone": 42,  # AP
	"national_id": 74,  # BV
	"national_id_expiry": 75,  # BW
	"si_status": 77,  # BY
	"si_number": 78,  # BZ
	"si_date": 79,  # CA
	"offer_status": 80,  # CB
	"status_date": 81,  # CC
	"remarks": 87,  # CI
	"file_index_start": 93,  # CO..DE document checklist
}


def text(ws, row: int, key: str) -> str:
	return " ".join(str(ws.cell(row, COL[key]).value or "").split())


def main() -> None:
	ws = openpyxl.load_workbook(SOURCE, data_only=True)["Master"]
	rows = [r for r in range(4, ws.max_row + 1) if text(ws, r, "work_id")]
	print(f"master employees: {len(rows)}")

	# --- vocabularies -----------------------------------------------------
	categories = collections.Counter(text(ws, r, "employment_category") for r in rows)
	contracts = collections.Counter(text(ws, r, "contract_type") for r in rows)
	sub_depts = collections.Counter(text(ws, r, "sub_department") for r in rows)

	def vocab(values: collections.Counter) -> list[dict]:
		return [
			{"name": name, "name_ar": None, "headcount": count}
			for name, count in sorted(values.items())
			if name
		]

	# --- designations -----------------------------------------------------
	# An English title can carry more than one Arabic spelling ("Secretary" ->
	# سكرتيرة / سكرتير / سكرته).  The most common is taken; the rest are reported
	# so the choice is visible rather than silent.
	designation_ar: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
	for r in rows:
		en, ar = text(ws, r, "position_en"), text(ws, r, "position_ar")
		if en and ar and en != ar:
			designation_ar[en][ar] += 1

	designations = []
	ambiguous = []
	for en, counter in sorted(designation_ar.items()):
		ar, count = counter.most_common(1)[0]
		entry = {"name": en, "name_ar": ar, "headcount": count}
		if len(counter) > 1:
			entry["note"] = (
				f"{len(counter)} Arabic spellings in the source; took the most common. "
				"Also seen: " + ", ".join(a for a, _ in counter.most_common()[1:])
			)
			ambiguous.append((en, dict(counter)))
		designations.append(entry)

	# English titles with no Arabic at all still need a Designation
	known = {d["name"] for d in designations}
	for r in rows:
		en = text(ws, r, "position_en")
		if en and en not in known:
			designations.append({"name": en, "name_ar": None, "headcount": 0})
			known.add(en)

	# "Starting Position Upon Joining the Company" (F) is a second, historical
	# title per employee.  32 of its 108 values never appear as a current
	# position, so they are added to the Designation vocabulary too - otherwise
	# the starting-designation link would point at nothing.  The source has no
	# Arabic for these, so name_ar stays None.
	starting_only = []
	for en, count in sorted(collections.Counter(text(ws, r, "starting_position") for r in rows).items()):
		if not en or en in known:
			continue
		designations.append(
			{
				"name": en,
				"name_ar": None,
				"headcount": count,
				"note": "Seen only as a starting position, never as a current one",
			}
		)
		known.add(en)
		starting_only.append(en)
	designations.sort(key=lambda d: d["name"])

	# NOTE: Employee also has a stock `employment_type` link.  The master has no
	# single column for it - "Employment Category" (V) is the seniority band and
	# is seeded as SDG Employment Category, so employment types are left alone
	# rather than guessed at.

	# --- company names ----------------------------------------------------
	# The master column is bilingual ("English - Arabic"); the payroll column is
	# English with typos plus one Arabic-only entry.  Both are carried through raw
	# and split by companies.split_bilingual at load time, so the normalisation
	# rule lives in one place.
	companies = []
	for name, count in sorted(collections.Counter(text(ws, r, "company") for r in rows).items()):
		if name:
			companies.append({"raw": name, "headcount": count})

	import openpyxl as _oxl

	pw = _oxl.load_workbook(
		pathlib.Path("/home/mahmoud/Master Payroll (corrected tax + PoD flag).xlsx"),
		data_only=True,
	).active
	payroll_companies = []
	for name, count in sorted(
		collections.Counter(
			" ".join(str(pw.cell(r, 5).value or "").split()) for r in range(3, 211) if pw.cell(r, 2).value
		).items()
	):
		if name:
			payroll_companies.append({"raw": name, "headcount": count})

	payload = {
		"_note": (
			"Generated by tools/build_master_fixture.py from Master Colleagues Data.xlsx. Do not hand edit."
		),
		"vocabularies": {
			"SDG Employment Category": vocab(categories),
			"SDG Contract Type": vocab(contracts),
			"SDG Sub Department": vocab(sub_depts),
		},
		"designations": designations,
		"companies": companies,
		"payroll_companies": payroll_companies,
	}

	OUT.parent.mkdir(parents=True, exist_ok=True)
	OUT.write_text(json.dumps(payload, indent=1, ensure_ascii=False))

	print(f"wrote {OUT}")
	for dt, items in payload["vocabularies"].items():
		print(f"  {dt:28} {len(items):>3} values")
	print(
		f"  {'Designation':28} {len(designations):>3} values"
		f"  ({len(starting_only)} of them starting-position only)"
	)
	print(f"  {'master companies':28} {len(companies):>3} values")
	print(f"  {'payroll companies':28} {len(payroll_companies):>3} values")
	print()
	if ambiguous:
		print(f"{len(ambiguous)} English title(s) have more than one Arabic spelling:")
		for en, counter in ambiguous:
			print(f"  {en}")
			for ar, n in sorted(counter.items(), key=lambda x: -x[1]):
				print(f"      {ar:32} {n}")
	else:
		print("every English title maps to exactly one Arabic spelling")


if __name__ == "__main__":
	main()
