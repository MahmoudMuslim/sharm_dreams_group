# Copyright (c) 2026, Sharm Dreams Group and contributors
# For license information, please see license.txt

"""Maps the master workbook's company names onto ERPNext's Company records.

`Company` is a Link field, so an employee cannot be imported until its source
name resolves to a real company.  The source names are messy in three different
ways, so the mapping is explicit and verified rather than fuzzy:

1.  The master's Company column is bilingual - ``"Concord For Touristic
    Development - كونكورد للتنمية السياحية"`` - sometimes with no space before
    the dash, and one row that has no dash at all.
2.  The payroll workbook's Company column holds English with typos
    ("Bautex misr", "Sharm Dreams Co . For Touristic investment", "IFLEET").
3.  Two companies appear in neither: ``Virgin Gates`` and ``Sharm Dreams
    Group``.

Matching is therefore: normalise both sides, then require an exact hit, then an
explicit override.  A fuzzy match is never used - a wrong company on an employee
is worse than a failed import.

Run:
    bench --site <site> execute sharm_dreams_group.companies.report
    bench --site <site> execute sharm_dreams_group.companies.seed_arabic_names
"""

from __future__ import annotations

import json
import pathlib
import re
import unicodedata

# The patterns below deliberately match the typography people actually type into
# the workbooks: a right single quote in "Co.", and en/em dashes as company-name
# separators.  Rewriting them to ASCII would stop them matching real data.
# ruff: noqa: RUF001
import frappe

FIXTURE = pathlib.Path(__file__).resolve().parent.parent / "seed_data" / "master_reference.json"

ARABIC_RE = re.compile(r"[؀-ۿ]")

# Workbook company names that do not match any Company record as written.
# Recorded explicitly so the gap is a decision rather than a silent omission:
# either the source names it in a way the site does not, or a translation is
# needed.
OVERRIDES: dict[str, str] = {
	# appears in the payroll only, and only in Arabic
	"فيرجن جيتس": "Virgin Gates",
	# the payroll uses a short form where the master carries the full bilingual
	# name.  Both of these Companies exist; only the payroll's spelling is
	# unresolvable, which is why the salary build halted on them.
	"IFLEET": "I Fleet Transport",
	"Afrika": "Africa Real Estate Investment",
}

# The parent holding company.  No employees are booked against it in either
# workbook, so there is no source name to map - it exists only as the group root.
NO_SOURCE = {"Sharm Dreams Group"}


# ---------------------------------------------------------------------------
# normalisation
# ---------------------------------------------------------------------------
def normalise(name: str) -> str:
	"""Fold a company name down to a comparable key.

	Lowercases, strips Arabic-script and punctuation noise, removes the tokens the
	sources sprinkle around ("company", "co"), and collapses whitespace.  The
	Arabic half of a bilingual master value is removed by `strip_arabic` first, so
	this only ever sees the English side.
	"""
	text = strip_arabic(name or "")
	text = unicodedata.normalize("NFKD", text)
	text = "".join(c for c in text if not unicodedata.combining(c))
	text = text.lower()
	text = text.replace("&", " and ")
	text = re.sub(r"[.'’]", "", text)
	text = re.sub(r"[^a-z0-9]+", " ", text)
	tokens = [t for t in text.split() if t not in {"company", "co", "ltd", "llc"}]
	return " ".join(tokens)


def strip_arabic(name: str) -> str:
	"""Drop the Arabic half of a bilingual company name.

	`"Sharm Dreams Co. For Touristic Investment- شركة شارم دريمز"` becomes
	`"Sharm Dreams Co. For Touristic Investment-"`.
	"""
	return ARABIC_RE.sub(" ", name or "")


def split_bilingual(name: str) -> tuple[str, str]:
	"""Return `(english, arabic)` for a master Company value.

	Both halves are identified by their script rather than by position, because
	the sources are inconsistent: most rows are ``"English - Arabic"``, one row
	has the halves jammed together with no dash
	(``"Africa Real Estate Investment Company شركة افريقا للاستثمار العقارى"``),
	and one has no space before the dash.

	Returning the whole cell as "arabic" when a dash is missing would store Latin
	words in `Company.company_name_ar`, so the Arabic side is always taken as
	Arabic-script runs only.
	"""
	clean = " ".join((name or "").split())
	english_parts: list[str] = []
	arabic_parts: list[str] = []

	for segment in re.split(r"\s*[-–—]\s*", clean):
		if not segment:
			continue
		for run in re.findall(r"[؀-ۿ][؀-ۿ\sء-ي]*", segment):
			arabic_parts.append(" ".join(run.split()))
		latin = ARABIC_RE.sub(" ", segment)
		latin = " ".join(re.sub(r"[^\w&]+", " ", latin).split())
		if latin:
			english_parts.append(latin)

	return " ".join(english_parts), " ".join(arabic_parts)


def is_pure_arabic(text: str) -> bool:
	"""True when `text` contains no Latin letters, i.e. it is safe to store in
	`Company.company_name_ar`."""
	return bool(ARABIC_RE.search(text or "")) and not re.search(r"[A-Za-z]", text or "")


# ---------------------------------------------------------------------------
# the map
# ---------------------------------------------------------------------------
def erpnext_companies() -> dict[str, str]:
	"""normalised name -> real Company name."""
	return {
		normalise(c): c for c in frappe.get_all("Company", fields=["name"], limit_page_length=0, pluck="name")
	}


def resolve(raw: str) -> str | None:
	"""A workbook company name -> the live Company, or None.

	The single place this mapping happens.  It exists because the workbooks name
	the same company three ways: bilingual in the master, English in payroll, and
	in one case Arabic only - which `OVERRIDES` already translates.

	Three copies of this logic grew up (the employee importer, the salary builder
	and an earlier draft), and the copies drifted: the salary one missed
	`OVERRIDES` and so failed to resolve the two Virgin Gates employees.
	"""
	if not raw:
		return None
	by_normalised = erpnext_companies()

	override = OVERRIDES.get(" ".join(raw.split()))
	if override:
		return by_normalised.get(normalise(override))

	english, _arabic = split_bilingual(raw)
	for candidate in (english, raw):
		if not candidate:
			continue
		hit = by_normalised.get(normalise(candidate))
		if hit:
			return hit
	return None


def build_map() -> tuple[dict[str, str], list[str]]:
	"""Return `({erpnext_company: arabic_name}, unresolved_company_names)`.

	Every company must either gain an Arabic name or be listed as unresolved, so
	nothing is quietly skipped.
	"""
	companies = erpnext_companies()
	arabic_for: dict[str, str] = {}

	def assign(english: str, arabic: str) -> None:
		key = normalise(english)
		target = companies.get(key)
		if not target:
			return
		existing = arabic_for.get(target)
		if existing and existing != arabic:
			print(f"  WARNING {target}: two Arabic names, keeping {existing!r} over {arabic!r}")
			return
		if not is_pure_arabic(arabic):
			# never write a half-English value into an Arabic field
			print(f"  WARNING {target}: rejected non-Arabic name {arabic!r}")
			return
		arabic_for[target] = arabic

	# 1. the master workbook's bilingual column
	data = json.loads(FIXTURE.read_text())
	for item in data.get("companies", []):
		english, arabic = split_bilingual(item["raw"])
		if english and arabic:
			assign(english, arabic)
		if arabic:
			# the master may name the company slightly differently from ERPNext
			for candidate, real in companies.items():
				if normalise(arabic) == candidate or normalise(item["raw"]) == candidate:
					arabic_for.setdefault(real, arabic)

	# 2. names that only exist in the payroll, some Arabic, some English typos
	for item in data.get("payroll_companies", []):
		raw = item["raw"]
		if ARABIC_RE.search(raw):
			real = OVERRIDES.get(" ".join(raw.split()))
			if real:
				arabic_for.setdefault(real, " ".join(raw.split()))
			continue
		real = companies.get(normalise(raw))
		if real and real not in arabic_for:
			english, arabic = split_bilingual(raw)
			if arabic:
				arabic_for[real] = arabic

	unresolved = [name for name in sorted(companies.values()) if name not in arabic_for]
	return arabic_for, unresolved


# ---------------------------------------------------------------------------
# entry points
# ---------------------------------------------------------------------------
def report() -> None:
	"""Print the mapping and anything that failed to resolve."""
	arabic_for, unresolved = build_map()
	companies = sorted(frappe.get_all("Company", fields=["name"], limit_page_length=0, pluck="name"))
	print(f"=== company map: {len(arabic_for)} of {len(companies)} resolved ===")
	for name in companies:
		if name in NO_SOURCE:
			tag = "  <- group parent, no source name (expected)"
		elif name in arabic_for:
			tag = f"  <- {arabic_for[name]}"
		else:
			tag = "  <- UNRESOLVED"
		print(f"  {name}{tag}")
	if unresolved:
		print(f"\n{len(unresolved)} company(ies) unresolved: {unresolved}")


def seed_arabic_names() -> None:
	"""Write the Arabic name onto each Company, refusing to proceed if any is
	unmapped beyond the known parent."""
	arabic_for, unresolved = build_map()
	unexpected = [name for name in unresolved if name not in NO_SOURCE]
	if unexpected:
		frappe.throw(
			frappe._(
				"No Arabic name for {0}. Add an override or correct the fixture before importing employees."
			).format(", ".join(unexpected)),
			title=frappe._("Unmapped company"),
		)

	created, updated = 0, 0
	for name, arabic in sorted(arabic_for.items()):
		existing = frappe.db.get_value("Company", name, "company_name_ar")
		if existing == arabic:
			continue
		if frappe.db.get_value("Company", name, "company_name_ar") is None:
			created += 1
		else:
			updated += 1
		frappe.db.set_value("Company", name, "company_name_ar", arabic)
	frappe.db.commit()
	frappe.clear_cache()

	print(f"company Arabic names: {created} set, {updated} corrected, {len(arabic_for)} mapped")
	for name in NO_SOURCE:
		if frappe.db.exists("Company", name):
			print(f"  {name}: no source name, left blank")
