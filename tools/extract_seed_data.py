#!/usr/bin/env python3
"""Extract seed data for sharm_dreams_group from the legacy source workbooks.

Runs on the host (where the .xlsx files live) and writes a single JSON that the
Frappe seeder consumes.  Kept separate from the seeder so the data can be
reviewed before anything is written to the database.

Usage:  python3 tools/extract_seed_data.py [OUTPUT.json]
"""

from __future__ import annotations

import collections
import json
import pathlib
import sys

import openpyxl

HOME = pathlib.Path("/home/mahmoud")
XLSX_DIR = pathlib.Path(__file__).resolve().parent.parent / "seed_data"

PAYROLL = HOME / "Master Payroll (corrected tax + PoD flag).xlsx"
MASTER = HOME / "Master Colleagues Data.xlsx"
MEDICAL = HOME / "Medical.xlsx"
COUPON = HOME / "Cost of living coupon.xlsx"
VACATION = HOME / "SharmDreamVacation Tracker.xlsx"


# ---------------------------------------------------------------------------
# sources
# ---------------------------------------------------------------------------
def payroll_rows():
	ws = openpyxl.load_workbook(PAYROLL, data_only=True)["Master Payroll"]
	for r in range(3, 210):
		wid = ws.cell(r, 2).value
		if not wid:
			continue
		yield {
			"work_id": str(wid).strip(),
			"row": r,
			"company": (ws.cell(r, 5).value or "").strip(),
			"delegated_company": (ws.cell(r, 6).value or "").strip() or None,
			"gross": ws.cell(r, 16).value or 0,
			# AB, the Total Earning column.  This is the figure a Salary Slip's
			# gross pay actually matches, and it is NOT always equal to `gross`:
			# the workbook has a pre-tax deduction block, so the two differ for
			# 67 of the 170 leader rows.
			"total_earning": ws.cell(r, 28).value or 0,
			"life_insurance": ws.cell(r, 34).value or 0,
			"medical": ws.cell(r, 35).value or 0,
			"si_base": ws.cell(r, 36).value or 0,
			"family_medical": ws.cell(r, 51).value or 0,
			"person_of_determination": (ws.cell(r, 68).value or "").strip() == "Yes",
			"medical_band": None,  # filled in from the register below
			"band_full": None,
			"staff_base": None,
			"dependents_in_register": 0,
			"spouse": False,
			"children": 0,
		}


def medical_register():
	"""national id -> {band, relation, staff_base, staff_code, dob, age}"""
	ws = openpyxl.load_workbook(MEDICAL, data_only=True)["Medical"]
	people, dependents = {}, collections.Counter()
	spouses, children_by_base = set(), collections.Counter()
	for r in range(2, ws.max_row + 1):
		nid = ws.cell(r, 19).value
		if not nid:
			continue
		staff = str(ws.cell(r, 18).value or "").strip()
		is_family = "_" in staff
		base = staff.split("_")[0]
		raw_type = " ".join(str(ws.cell(r, 6).value or "").split()).strip()
		parts = raw_type.split(" ", 1)
		rec = {
			"band": (parts[0] if parts and parts[0] else None),
			"band_tier": (parts[1].upper() if len(parts) > 1 else ""),
			"band_full": raw_type or None,
			"relation": ws.cell(r, 15).value,
			"staff_code": staff,
			"staff_base": base,
			"is_family": is_family,
			"dob": str(ws.cell(r, 14).value) if ws.cell(r, 14).value else None,
			"age": ws.cell(r, 17).value,
		}
		people[str(nid).strip()] = rec
		if is_family:
			dependents[base] += 1
			if rec["relation"] == "SPOUSE":
				spouses.add(base)
			elif rec["relation"] == "CHILD":
				children_by_base[base] += 1
	return people, dependents, spouses, children_by_base


def coupon_tiers():
	"""(category ar) -> amount, read from the summary block of the coupon sheet."""
	ws = openpyxl.load_workbook(COUPON, data_only=True)["Cost of living coupon 5-2026"]
	out = []
	for r in range(4, 8):
		per_person = ws.cell(r, 4).value
		if per_person is None:
			continue
		ar = str(ws.cell(r, 5).value or "").strip()
		mapped = COUPON_CATEGORY.get(ar)
		if not mapped:
			raise SystemExit(f"unmapped coupon category in the source sheet: {ar!r}")
		category, marital, children = mapped
		out.append(
			{
				"category": category,
				"marital_status": marital,
				"children_count": children,
				"category_ar": ar,
				"coupon_amount": float(per_person),
				"headcount": int(float(ws.cell(r, 2).value or 0)),
				"stated_total": float(ws.cell(r, 3).value or 0),
			}
		)
	return out


def coupon_entitlements():
	ws = openpyxl.load_workbook(COUPON, data_only=True)["Cost of living coupon 5-2026"]
	out = {}
	for r in range(10, ws.max_row + 1):
		wid = ws.cell(r, 2).value
		if wid and ws.cell(r, 6).value is not None:
			out[str(wid).strip()] = float(ws.cell(r, 6).value)
	return out


def leave_codes():
	ws = openpyxl.load_workbook(VACATION, data_only=True)["Employee Vacation Tracker 2026"]
	counts = collections.Counter()
	for r in range(4, ws.max_row + 1):
		for c in range(17, 90):
			v = ws.cell(r, c).value
			if isinstance(v, str) and v.strip():
				counts[v.strip()] += 1
	return counts


# ---------------------------------------------------------------------------
# derivations
# ---------------------------------------------------------------------------
COUPON_CATEGORY = {
	"اعزب": ("Single", "Single", 0),
	"متزوج/متزوج ويعول 1 طفل": ("Married + 1 Child", "Married", 0),
	"متزوج ويعول 2 طفل": ("Married + 2 Children", "Married", 2),
	"متزوج ويعول 3 طفل": ("Married + 3 Children", "Married", 3),
	"متزوج": ("Married", "Married", 0),
}


def build_medical_bands(rows):
	"""The insurer issues three coverage bands.  The premium per band is the
	value that actually appears against that band across the payroll.

	Where the sample is thin, or the band carries more than one premium, the row
	is emitted with `needs_review` so the seeder refuses to treat it as settled.
	"""
	by_band = collections.defaultdict(list)
	for r in rows:
		if r["band_full"] and r["medical"] > 0:
			by_band[r["band_full"]].append(r["medical"])
	out = []
	for band, values in by_band.items():
		counts = collections.Counter(values)
		# "75k C شارم دريمز" -> "75k C"  (the Arabic suffix is the policy holder)
		clean = " ".join(part for part in band.split() if not any("؀" <= ch <= "ۿ" for ch in part))
		premium, n = counts.most_common(1)[0]
		limit_txt = clean.split(" ")[0].rstrip("kK")
		try:
			limit = float(limit_txt) * 1000
		except ValueError:
			limit = 0
		out.append(
			{
				"band": clean,
				"band_raw": band,
				"coverage_limit": limit,
				"monthly_premium": premium,
				"sample_size": n,
				"distribution": {str(k): v for k, v in sorted(counts.items())},
				"needs_review": n < 10 or len(counts) > 1,
			}
		)
	out.sort(key=lambda d: d["coverage_limit"])
	return out


def build_gross_bands(rows):
	"""One band per distinct total earning that appears, so the table reproduces
	the legacy workbook exactly.  `to_gross` is the next distinct total earning
	(exclusive); the top band is open ended.  Flagged for replacement by the real
	pay scale.

	Two things this must get right, both of which were wrong before:

	* the band key is **Total Earning (AB)**, not Gross Salary.  A Salary Slip's
	  gross pay matches Total Earning, and because the workbook deducts
	  penalties/absenteeism before it, Gross Salary is a different number for 67
	  of the 170 employees.  Keying on Gross Salary put 51 employees in the wrong
	  band.
	* **delegated cost-share rows are excluded.**  A manager who is cost-charged
	  to other companies has one extra row per charge, and those rows carry no
	  social insurance of their own: including all 37 of them injected 36 bands
	  with an si_base of 0 and pushed real employees out of their band.
	"""
	seen = {}
	for r in rows:
		if r.get("delegated_company"):
			continue
		g = round(float(r.get("total_earning") or 0), 2)
		if g <= 0:
			continue
		si = round(float(r.get("si_base") or 0), 6)
		life = round(float(r.get("life_insurance") or 0), 6)
		if g in seen and seen[g][1] != si:
			raise ValueError(
				f"total earning {g:,.2f} has two different social insurance bases "
				f"({seen[g][1]:,.2f} and {si:,.2f}), so it cannot be a band"
			)
		seen.setdefault(g, (life, si))
	grosses = sorted(seen)
	out = []
	for i, g in enumerate(grosses):
		life, si = seen[g]
		nxt = grosses[i + 1] if i + 1 < len(grosses) else 0  # 0 => open ended
		out.append(
			{
				"from_gross": g,
				"to_gross": nxt if nxt else 9_999_999_999,
				"life_insurance": life,
				"si_base": si,
			}
		)
	return out


def build_family_bands(rows, band_alias):
	"""Observed (band, spouse, children) -> family premium combinations."""
	seen = {}
	for r in rows:
		if not r["family_medical"] or not r["medical_band"]:
			continue
		key = (band_alias.get(r["band_full"], r["band_full"]), bool(r["spouse"]), int(r["children"] or 0))
		seen.setdefault(key, []).append(float(r["family_medical"]))
	out = []
	for (band, spouse, children), values in sorted(seen.items()):
		premium = collections.Counter(values).most_common(1)[0][0]
		out.append(
			{
				"medical_band": band,
				"has_spouse": 1 if spouse else 0,
				"children_count": children,
				"monthly_premium": premium,
				"sample_size": len(values),
			}
		)
	return out


TAX_BRACKETS = [
	{"up_to": 40000, "rate": 0, "fixed_amount": 0},
	{"up_to": 55000, "rate": 10, "fixed_amount": 0},
	{"up_to": 70000, "rate": 15, "fixed_amount": 1500},
	{"up_to": 200000, "rate": 20, "fixed_amount": 3750},
	{"up_to": 400000, "rate": 22.5, "fixed_amount": 29750},
	{"up_to": 1200000, "rate": 25, "fixed_amount": 74750},
	# open ended: everything above 1.2m is charged 27.5% on the excess, which
	# means 274,750 is already due at the 1.2m threshold.
	{"up_to": 9999999999, "rate": 27.5, "fixed_amount": 274750, "is_last_band": True},
]

# Provisional.  Needs payroll sign-off - flagged in the seeder output.
LEAVE_TYPES = [
	("V", "Vacation", 1, 0),
	("LL", "Long Leave (unpaid)", 0, 1),
	("CL", "Casual Leave", 1, 0),
	("SL", "Sick Leave", 1, 0),
	("DO", "Day Off", 1, 0),
	("PDO", "Public Day Off", 1, 0),
	("PH", "Public Holiday", 1, 0),
	("DE-L", "Detached Leave", 0, 1),
	("D.L", "Deputation Leave", 0, 1),
	("DI.L", "Detached/Deputation Leave", 0, 1),
	("PDOPH", "Public Day Off / Public Holiday", 1, 0),
	("LLPH", "Long Leave + Public Holiday", 0, 1),
]


def main() -> None:
	rows = list(payroll_rows())
	people, dependents, spouses, children_by_base = medical_register()

	# enrich payroll rows with the insurer's view
	national = {}
	ws = openpyxl.load_workbook(PAYROLL, data_only=True)["Master Payroll"]
	for r in range(3, 210):
		wid = ws.cell(r, 2).value
		nid = ws.cell(r, 10).value
		if wid and nid:
			national[str(wid).strip()] = str(int(nid)).strip()
	for r in rows:
		rec = people.get(national.get(r["work_id"], ""))
		if rec:
			r["medical_band"] = rec["band_full"]
			r["band_full"] = rec["band_full"]
			r["staff_base"] = rec["staff_base"]
			r["spouse"] = rec["staff_base"] in spouses
			r["children"] = children_by_base.get(rec["staff_base"], 0)
			r["dependents_in_register"] = dependents.get(rec["staff_base"], 0)

	medical_bands = build_medical_bands(rows)
	band_alias = {b["band_raw"]: b["band"] for b in medical_bands}

	data = {
		"_meta": {
			"source_payroll": str(PAYROLL),
			"rows": len(rows),
			"note": "Generated by tools/extract_seed_data.py - review before seeding",
		},
		"medical_bands": medical_bands,
		"gross_insurance_bands": build_gross_bands(rows),
		"family_medical_bands": build_family_bands(rows, band_alias),
		"coupon_bands": coupon_tiers(),
		"coupon_entitlements": coupon_entitlements(),
		"tax_brackets": TAX_BRACKETS,
		"leave_types": [
			{"code": c, "leave_type": t, "is_paid": p, "affects_deduction": d} for c, t, p, d in LEAVE_TYPES
		],
		"leave_code_counts": dict(leave_codes().most_common()),
		"payroll_settings": {
			"si_employee_rate": 11,
			"si_employer_rate": 18.75,
			"training_fund_rate": 0.25,
			"training_fund_fixed": 0,
			"martyrs_fund_rate": 0.05,
			"emergency_fund_rate": 1,
			"emergency_base_cap": 2720,
			"exemption_standard": 1666.66,
			"exemption_determination": 2500,
			"delegated_tax_rate": 10,
			"tax_rounding_multiple": 10,
			"insurance_cap_percent": 15,
			"insurance_cap_max": 10000,
			"monthly_days_divisor": 30,
			"official_day_multiplier": 2,
			"family_member_annual_rate": 3100,
		},
		"loan_settings": {
			"default_interest_rate": 0,
			"default_term_months": 12,
			"short_deduction_policy": "Cap at net pay",
			"allow_early_settlement": 1,
			"stop_on_relieving": 1,
		},
		"insured_people": len(people),
	}

	out = pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 else XLSX_DIR / "seed.json"
	out.parent.mkdir(parents=True, exist_ok=True)
	out.write_text(json.dumps(data, indent=1, ensure_ascii=False))

	print(f"wrote {out}")
	print(f"  medical bands          : {len(data['medical_bands'])}")
	for b in data["medical_bands"]:
		flag = "  <== NEEDS REVIEW" if b["needs_review"] else ""
		print(
			f"      {b['band']:10} limit={b['coverage_limit']:>9,.0f} premium={b['monthly_premium']:>8,.2f} n={b['sample_size']:>3} {b['distribution']}{flag}"
		)
	print(f"  gross insurance bands  : {len(data['gross_insurance_bands'])}")
	print(f"  family medical bands   : {len(data['family_medical_bands'])}")
	for b in data["family_medical_bands"]:
		print(
			f"      band={b['medical_band']:5} spouse={b['has_spouse']} children={b['children_count']} -> {b['monthly_premium']:,.2f}"
		)
	print(f"  coupon tiers           : {len(data['coupon_bands'])}")
	for c in data["coupon_bands"]:
		drift = c["stated_total"] - c["headcount"] * c["coupon_amount"]
		note = f"  <== stated total off by {drift:,.0f}" if abs(drift) > 0.5 else ""
		print(f"      {c['category']:22} {c['coupon_amount']:>8,.0f} x{c['headcount']:>3}{note}")
	print(f"  tax brackets           : {len(data['tax_brackets'])}")
	print(f"  leave types            : {len(data['leave_types'])}")
	print(f"  insured persons        : {data['insured_people']}")


if __name__ == "__main__":
	main()
