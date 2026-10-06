"""Build `seed_data/medical_register.json` from `Medical.xlsx`.

The payroll workbook carries a monthly medical premium per employee, but it is a
payroll figure, not a register: it says what was charged, not who is covered.  The
register is the insurer's own file, and it says who - the employee and their
dependants, on which plan.

One record per **employee**, matched to the Employee by national ID, carrying the
family composition, because `SDG Insured Person` is one row per person.  The
dependant rows in the register have no national ID of their own that we can rely
on, so they are attributed to the principal on the row above, which is how the
insurer lays the file out.

Run on the host:

    python3 tools/build_medical_register.py
"""

from __future__ import annotations

import json
import pathlib
from collections import Counter

import openpyxl

SOURCE = pathlib.Path("/home/mahmoud/Medical.xlsx")
EMPLOYEES = pathlib.Path(__file__).resolve().parent.parent / "seed_data" / "employees.json"
OUT = pathlib.Path(__file__).resolve().parent.parent / "seed_data" / "medical_register.json"

COL = {
	"company": 4,  # D
	"card": 3,  # C  EXTERNAL REFERENCE
	"plan": 6,  # F  Type
	"staff": 18,  # R  STAFF#
	"national_id": 19,  # S  NATIONAL ID
	"card_expiry": 2,  # B  EXPIRY DATE
	"first_eligibility": 12,  # L
	"starting_date": 13,  # M
	"date_of_birth": 14,  # N
	"relation": 15,  # O
	"sex": 16,  # P
	"age": 17,  # Q
	"comment": 20,  # T
}

PLAN_SUFFIX = "شارم دريمز"


def _text(ws, r: int, key: str) -> str:
	value = ws.cell(r, COL[key]).value
	return str(value).strip() if value is not None else ""


def _date(ws, r: int, key: str) -> str | None:
	value = ws.cell(r, COL[key]).value
	return value.strftime("%Y-%m-%d") if hasattr(value, "strftime") else None


def _band(plan: str) -> str | None:
	"""'75k C شـارم دريمز' -> '75k C', the name `SDG Medical Band` is keyed on."""
	band = plan.split()[0:2]
	candidate = " ".join(band).strip()
	return candidate or None


def build() -> dict:
	employees = json.loads(EMPLOYEES.read_text())
	records = employees["employees"] if isinstance(employees, dict) else employees
	by_nid = {str(r.get("national_id") or "").strip(): r for r in records}

	ws = openpyxl.load_workbook(SOURCE, data_only=True)["Medical"]
	people: dict[str, dict] = {}
	dependants = Counter()
	unmatched = 0
	current: str | None = None

	for r in range(2, ws.max_row + 1):
		nid = _text(ws, r, "national_id")
		relation = _text(ws, r, "relation").upper()
		plan = _text(ws, r, "plan")
		if not nid and not relation:
			continue

		if relation == "PRINCIPAL":
			current = None
			if not nid or nid not in by_nid:
				unmatched += 1
				continue
			employee = by_nid[nid]
			current = nid
			people[nid] = {
				"national_id": nid,
				"employee_number": employee.get("employee_number"),
				"work_id": employee.get("employee_number"),
				"si_number": employee.get("si_number"),
				"medical_band": _band(plan),
				"insurer_name": _band(plan),
				"card_number": _text(ws, r, "card") or None,
				"card_expiry": _date(ws, r, "card_expiry"),
				"national_id_expiry": _date(ws, r, "card_expiry"),
				"si_date": _date(ws, r, "starting_date"),
				"has_spouse": 0,
				"dependants_count": 0,
				"company": _text(ws, r, "company"),
				"active": 1,
				"remarks": None,
			}
		elif current and relation in ("SPOUSE", "CHILD"):
			# the insurer lists dependants under their principal
			people[current]["dependants_count"] += 1
			if relation == "SPOUSE":
				people[current]["has_spouse"] = 1
			dependants[relation] += 1
		elif relation == "PRINCIPAL":
			unmatched += 1

	for person in people.values():
		if person["dependants_count"] and not person["remarks"]:
			person["remarks"] = f"{person['dependants_count']} dependant(s) on the register"

	return {
		"_meta": {
			"source": SOURCE.name,
			"register_rows": ws.max_row - 1,
			"principals_matched": len(people),
			"principals_not_matched": unmatched,
			"spouses": dependants["SPOUSE"],
			"children": dependants["CHILD"],
		},
		"people": sorted(people.values(), key=lambda p: p["national_id"]),
	}


def main() -> int:
	data = build()
	OUT.parent.mkdir(parents=True, exist_ok=True)
	OUT.write_text(json.dumps(data, indent=1, ensure_ascii=False))
	meta = data["_meta"]
	print(f"wrote {OUT}")
	print(f"  register rows            : {meta['register_rows']}")
	print(f"  matched to our employees : {meta['principals_matched']}")
	print(f"  principals not ours      : {meta['principals_not_matched']}")
	print(f"  spouses / children       : {meta['spouses']} / {meta['children']}")
	print(f"  with dependants          : {sum(1 for p in data['people'] if p['dependants_count'])}")
	print("  bands                    :", dict(Counter(p["medical_band"] for p in data["people"])))
	return 0


if __name__ == "__main__":
	raise SystemExit(main())
