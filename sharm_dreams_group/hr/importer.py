# Copyright (c) 2026, Sharm Dreams Group and contributors
# For license information, please see license.txt

"""Import employees from `seed_data/employees.json`, built by tools/build_employee_fixture.py.

The fixture is a *plan*, not a script: it carries one record per person with the
raw source strings, the provenance of each value and any flags.  This module
resolves those strings against the live site, validates the whole set, and only
then writes anything.

Two phases on purpose:

    build_plan()   resolve and validate, touch nothing
    run()          write the plan

`run()` refuses to write if the plan contains a single error.  Partial imports
are the worst outcome here: 182 employees, half-created, with no record of which
half.  So every record is resolved first and the errors are reported together.

Matching an existing Employee is on `sdg_national_id`, the National ID, because
`employee_number` can be corrected by HR while the National ID is also the join
key to the payroll and medical files.  The number is still the fallback.

Run:
    bench --site <site> execute sharm_dreams_group.hr.importer.plan
    bench --site <site> execute sharm_dreams_group.hr.importer.run
"""

from __future__ import annotations

import json
import pathlib
import re
from html import escape as _escape_html

import frappe

from sharm_dreams_group import companies as companies_mod
from sharm_dreams_group import vocabularies
from sharm_dreams_group.hr.employee import PHONE_PATTERN, save_employee

# This module sits one level deeper than the app's other modules (hr/), so the
# app root is three parents up, not two.
FIXTURE = pathlib.Path(__file__).resolve().parent.parent.parent / "seed_data" / "employees.json"


# Per the agreed policy: these have no payroll row, so there is no evidence they
# are still employed, but HR decided to import them as active.
STATUS_MASTER_ONLY = "Active"
STATUS_DEFAULT = "Active"


def escape_html(text: str) -> str:
	return _escape_html(text, quote=False)


GENDER = {"Male": "Male", "Female": "Female"}

# Spellings in the source files that differ from the site's record.  Each entry
# is an explicit, reviewed mapping rather than fuzzy matching: fuzzy matching on
# job titles would silently attach people to the wrong designation, which is
# worse than a visible error.  A corrected spelling is flagged on the record so
# HR can see it was not taken at face value.
DESIGNATION_TYPOS = {
	"Jonior Web Developer": "Junior Web Developer",
}


def seed_departments() -> dict:
	"""Create the Departments the master uses, one per company.

	`Department.company` is mandatory, so an ERPNext department belongs to exactly
	one company.  The master's Department column is group-wide - "Finance" is the
	same name under several companies - so it expands to 58 (department, company)
	pairs, which is the shape ERPNext expects.  Each employee is then linked to
	the department in their own company.

	Matched case-insensitively, so "Vacation club" reuses the 30-person
	"Vacation Club" instead of creating a one-person duplicate.  Names that differ
	only by a suffix are left as separate rows and reported, because merging them
	is an org-structure decision rather than a spelling one.
	"""
	data = load_fixture()
	companies = Resolver().companies  # normalised key -> Company name

	pairs: dict[tuple[str, str], int] = {}
	for record in data["employees"]:
		department = record.get("department")
		if not department:
			continue
		company = companies.get(companies_mod.normalise(company_english(record["company_source"])))
		if not company:
			continue  # reported as an unresolved company by build_plan()
		pairs[(department, company)] = pairs.get((department, company), 0) + 1

	created, reused = [], []
	for (department, company), count in sorted(pairs.items()):
		existing = _find_department(department, company)
		if existing:
			used = frappe.db.get_value("Department", existing, "department_name")
			if (used or department) != department:
				reused.append({"wanted": department, "used": used, "company": company})
			continue
		frappe.get_doc(
			{
				"doctype": "Department",
				"department_name": department,
				"company": company,
				"is_group": 0,
			}
		).insert(ignore_permissions=True)
		created.append({"department": department, "company": company, "employees": count})

	frappe.db.commit()

	# names differing only by a trailing word: possible duplicates, flagged
	possible = []
	names = sorted({d for d, _c in pairs})
	for i, a in enumerate(names):
		for b in names[i + 1 :]:
			if a.lower() != b.lower() and (
				a.lower().removesuffix(" department") == b.lower()
				or b.lower().removesuffix(" department") == a.lower()
			):
				possible.append((a, b))

	print(f"=== Departments ({len(pairs)} department/company pairs) ===")
	for row in created:
		print(f"  created {row['department']:30} @ {row['company'][:34]:36} ({row['employees']})")
	for row in reused:
		print(f"  reused  {row['wanted']:30} -> {row['used']} @ {row['company'][:34]}")
	print(f"  {len(created)} created, {len(pairs) - len(created)} already present")
	if possible:
		print("  possible duplicates, both were kept - review:")
		for a, b in possible:
			print(f"     {a!r} and {b!r}")
	return {"created": created, "reused": reused, "possible_duplicates": possible}


def company_english(raw: str) -> str:
	"""The English half of a bilingual company name."""
	english, _arabic = companies_mod.split_bilingual(raw)
	return english or raw


def _find_department(name: str, company: str) -> str | None:
	"""The Department *document name* for a department within a company.

	ERPNext names a Department "<department_name> - <company abbreviation>", so
	"Finance" in Bautex Misr is the document `Finance - BM`.  That composite is
	the Link value, and Employee.department has to hold it - passing the plain
	department name raises LinkValidationError.
	"""
	exact = frappe.db.get_value("Department", {"department_name": name, "company": company}, "name")
	if exact:
		return exact
	for row in frappe.get_all(
		"Department",
		filters={"company": company},
		fields=["name", "department_name"],
		limit_page_length=0,
	):
		if (row.department_name or "").lower() == name.lower():
			return row.name
	return None


# The master's job profile is a bulleted list typed into a spreadsheet cell:
# lines starting with a bullet, a dash or a number.  Employee's field is a Text
# Editor, i.e. HTML, so a plain string would render as one collapsed paragraph.
BULLET = re.compile(r"^\s*(?:[\u2022\u25cf\u25aa\u2023\-\*]|\d+[\.\)])\s*")


def job_profile_html(raw: str) -> str:
	"""Turn the source cell into HTML for a Text Editor.

	Consecutive bullet lines become one <ul>; anything else becomes a paragraph.
	Built from the source text every time, so re-importing cannot double-wrap
	already-converted content.
	"""
	raw = (raw or "").replace("\r\n", "\n").replace("\r", "\n").strip()
	if not raw:
		return ""

	# 65 of the 95 cells put every bullet on one physical line
	# ("Train marketing staff. \u2022 Monitor customer attendance ..."), so the
	# bullet glyphs have to become line breaks before the line pass runs, and the
	# glyph is kept so the line below still reads as a list item.  Only
	# the glyphs: a dash or a number is a list marker at the start of a line but
	# ordinary punctuation in the middle of one, and turning those into breaks
	# would shred the text.
	raw = re.sub(r"\s*([\u2022\u25cf\u25aa\u2023])\s*", r"\n\1", raw)

	items: list[str] = []
	paragraphs: list[str] = []

	def flush_items() -> None:
		if items:
			items.insert(0, "<ul>")
			items.append("</ul>")
			paragraphs.append("".join(items))
			items.clear()

	lines = [line.strip() for line in raw.split("\n")]
	for line in lines:
		if not line:
			flush_items()
			continue
		if BULLET.match(line):
			items.append(f"<li>{escape_html(BULLET.sub('', line))}</li>")
		else:
			flush_items()
			paragraphs.append(f"<p>{escape_html(line)}</p>")
	flush_items()
	return "".join(paragraphs)


def first_phone(raw: str) -> str:
	"""The first usable phone number in a cell, or "".

	Several source cells hold more than one number joined by a dash
	("1100321125-01009641197"), and Employee's phone fields reject that outright
	with InvalidPhoneNumberError rather than truncating.  Taking the first valid
	number keeps the value usable; anything dropped is reported so nothing
	silently loses a second number.
	"""
	if not raw:
		return ""
	for match in PHONE_PATTERN.finditer(raw):
		number = match.group(0)
		# the pattern also matches inside longer digit runs, so re-check with a
		# boundary on both sides
		if match.start() and raw[match.start() - 1].isdigit():
			continue
		if match.end() < len(raw) and raw[match.end()].isdigit():
			continue
		return number
	return ""


# ---------------------------------------------------------------------------
# fixture
# ---------------------------------------------------------------------------
def load_fixture() -> dict:
	if not FIXTURE.exists():
		raise FileNotFoundError(
			f"{FIXTURE} not found. Run tools/build_employee_fixture.py on the host first."
		)
	return json.loads(FIXTURE.read_text())


# ---------------------------------------------------------------------------
# resolution
# ---------------------------------------------------------------------------
class Resolver:
	"""Turns the fixture's raw source strings into live document names.

	Company names arrive in several shapes (bilingual in the master, English in
	payroll, Arabic only in one row), so they go through the same normaliser the
	company seeding used.  Designations are matched case-insensitively by
	`vocabularies.resolve_existing` so the curated rows are reused rather than
	duplicated.
	"""

	def __init__(self):
		self.companies = companies_mod.erpnext_companies()
		self._by_normalised = dict(self.companies)
		self._designations = {
			d.lower(): d for d in frappe.get_all("Designation", pluck="name", limit_page_length=0)
		}
		self.unresolved: dict[str, set] = {}

	def company(self, raw: str) -> str | None:
		"""Delegate to the one place this mapping is implemented."""
		hit = companies_mod.resolve(raw)
		if not hit and raw:
			self.unresolved.setdefault("company", set()).add(raw.strip())
		return hit

	def designation(self, raw: str) -> str | None:
		if not raw:
			return None
		corrected = DESIGNATION_TYPOS.get(raw, raw)
		hit = self._designations.get(corrected.lower())
		if hit:
			return hit
		hit = vocabularies.resolve_existing("Designation", corrected)
		if hit:
			return self._designations.setdefault(hit.lower(), hit)
		self.unresolved.setdefault("designation", set()).add(raw.strip())
		return None

	def vocabulary(self, doctype: str, raw: str) -> str | None:
		"""Link to one of the SDG vocabularies; empty stays empty."""
		if not raw:
			return None
		hit = vocabularies.resolve_existing(doctype, raw)
		if not hit:
			self.unresolved.setdefault(doctype, set()).add(raw.strip())
		return hit

	def department(self, raw: str, company: str | None) -> str | None:
		"""Employee.department is a stock Link, scoped to the employee's company."""
		if not raw:
			return None
		if not company:
			# the company is reported separately; saying nothing here avoids
			# listing every department as unresolved when the company is the gap
			return None
		hit = _find_department(raw, company)
		if not hit:
			self.unresolved.setdefault("department", set()).add(f"{raw} (in {company})")
		return hit

	def branch(self, raw: str) -> str | None:
		"""Employee.branch is a stock Link too, though all of them already exist."""
		if not raw:
			return None
		hit = vocabularies.resolve_existing("Branch", raw)
		if not hit:
			self.unresolved.setdefault("branch", set()).add(raw.strip())
		return hit


# ---------------------------------------------------------------------------
# validation
# ---------------------------------------------------------------------------
def _required(record: dict, *keys) -> list[str]:
	return [f"{k} is empty" for k in keys if not record.get(k)]


def validate(record: dict, resolved: dict, advisories: list[str]) -> list[str]:
	"""Everything that would make this record wrong or unimportable.

	Kept separate from building the document so the same checks can run in the
	dry run and again immediately before writing.
	"""
	errors: list[str] = []
	who = record["employee_number"]

	errors += [f"{who}: {msg}" for msg in _required(record, "employee_number", "first_name")]

	if not record.get("national_id"):
		errors.append(f"{who}: no National ID, cannot be matched to payroll or medical")
	elif len(record["national_id"]) not in (13, 14, 15):
		# not fatal, but suspicious enough to say out loud
		errors.append(f"{who}: National ID {record['national_id']} is an odd length")

	if not record.get("date_of_joining"):
		# Not fatal: HR decided to import the master-only employees as active, and
		# a missing joining date only leaves tenure blank.  Reported, not blocked.
		advisories.append(f"{who}: no joining date, so tenure will be empty")

	for label, value in (("company", resolved.get("company")), ("designation", resolved.get("designation"))):
		if record.get(f"{label}_source") and not value:
			errors.append(f"{who}: {label} {record[f'{label}_source']!r} does not match any {label}")

	for label in ("department", "branch"):
		source = record.get(label)
		if source and not resolved.get(label):
			errors.append(
				f"{who}: {label} {source!r} does not exist; "
				f"run sharm_dreams_group.hr.importer.seed_departments first"
				if label == "department"
				else f"{who}: {label} {source!r} does not exist"
			)

	joining = record.get("date_of_joining")
	dob = record.get("date_of_birth")
	if joining and dob and joining <= dob:
		# Employee.validate_dates rejects this, and it should: it means a
		# transcription error, and guessing which of the two is wrong would put
		# wrong tenure and wrong tax exemption into a payroll run.
		errors.append(
			f"{who}: joining date {joining} is not after date of birth {dob}; "
			"one of the two source values is wrong"
		)

	# a duplicate National ID would break the join to payroll and medical
	if "duplicate_national_id" in record.get("flags", []):
		errors.append(
			f"{who}: National ID {record['national_id']} is shared with another employee; "
			"one of the two source values is wrong and this must be resolved first"
		)
	return errors


# ---------------------------------------------------------------------------
# plan
# ---------------------------------------------------------------------------
def build_plan() -> dict:
	"""Resolve and validate every record.  Writes nothing."""
	data = load_fixture()
	resolver = Resolver()

	plan: list[dict] = []
	errors: list[str] = []
	warnings: list[str] = []

	existing_by_nid = {
		row.sdg_national_id: row.name
		for row in frappe.get_all(
			"Employee",
			filters={"sdg_national_id": ("is", "set")},
			fields=["name", "sdg_national_id"],
			limit_page_length=0,
		)
	}
	existing_by_number = {
		name: name for name in frappe.get_all("Employee", pluck="name", limit_page_length=0)
	}

	seen_numbers: set[str] = set()
	seen_nids: set[str] = set()

	for record in data["employees"]:
		who = record["employee_number"]

		if who in seen_numbers:
			errors.append(f"{who}: appears more than once in the fixture")
			continue
		seen_numbers.add(who)

		nid = record["national_id"]
		if nid and nid in seen_nids:
			errors.append(f"{who}: National ID {nid} appears more than once in the fixture")
		elif nid:
			seen_nids.add(nid)

		resolved = {
			"company": resolver.company(record["company_source"]),
			"designation": resolver.designation(record["designation_source"]),
			"starting_designation": resolver.designation(record["starting_designation_source"]),
			"employment_category": resolver.vocabulary(
				"SDG Employment Category", record["employment_category"]
			),
			"contract_type": resolver.vocabulary("SDG Contract Type", record["contract_type"]),
			"sub_department": resolver.vocabulary("SDG Sub Department", record["sub_department"]),
			"department": resolver.department(
				record.get("department", ""), resolver.company(record["company_source"])
			),
			"branch": resolver.branch(record.get("branch", "")),
		}
		# a designation that resolves is also a valid Employee Grade
		resolved["grade"] = record["grade"] or None

		# a spelling we corrected, so HR can see it was not taken at face value
		if record["designation_source"] in DESIGNATION_TYPOS:
			record.setdefault("flags", []).append(
				f"designation_spelling_corrected: {record['designation_source']}"
				f" -> {DESIGNATION_TYPOS[record['designation_source']]}"
			)

		advisories: list[str] = []
		errors += validate(record, resolved, advisories)
		warnings.extend(advisories)

		target = existing_by_nid.get(nid) or (existing_by_number.get(who) if nid else None)
		action = "update" if target else "insert"
		if not action == "update" and who in existing_by_number:
			target, action = existing_by_number[who], "update"

		if record["source"] == "master" and "no_payroll_record" in record.get("flags", []):
			status = STATUS_MASTER_ONLY
		else:
			status = STATUS_DEFAULT
		if record.get("offer_status") == "Off board":
			status = "Left"

		plan.append(
			{
				"employee_number": who,
				"national_id": nid,
				"action": action,
				"existing": target,
				"source": record["source"],
				"status": status,
				"flags": record.get("flags", []),
				"record": record,
				"resolved": resolved,
			}
		)

		# advisories: worth reporting, not worth refusing to import over
		if not record.get("bank_account"):
			warnings.append(f"{who}: no bank account, so payroll will not be able to pay them")
		raw_phone = record.get("cell_number", "")
		if raw_phone and not first_phone(raw_phone):
			warnings.append(f"{who}: telephone {raw_phone!r} is not a usable number, left empty")
		elif first_phone(raw_phone) != raw_phone.strip():
			warnings.append(f"{who}: telephone {raw_phone!r} reduced to {first_phone(raw_phone)!r}")
		if record["source"] == "payroll":
			warnings.append(f"{who}: no master record, imported from payroll alone - review needed")

	return {
		"plan": plan,
		"errors": errors,
		"warnings": sorted(set(warnings)),
		"unresolved": {k: sorted(v) for k, v in resolver.unresolved.items()},
		"summary": data["summary"],
	}


# ---------------------------------------------------------------------------
# reporting
# ---------------------------------------------------------------------------
def report(result: dict) -> None:
	plan = result["plan"]
	by_action: dict[str, int] = {}
	by_source: dict[str, int] = {}
	for item in plan:
		by_action[item["action"]] = by_action.get(item["action"], 0) + 1
		by_source[item["source"]] = by_source.get(item["source"], 0) + 1

	print("=== employee import plan ===")
	print(f"  records        : {len(plan)}")
	print(f"  insert / update: {by_action.get('insert', 0)} / {by_action.get('update', 0)}")
	print(f"  from both      : {by_source.get('both', 0)}")
	print(f"  master only    : {by_source.get('master', 0)}")
	print(f"  payroll only   : {by_source.get('payroll', 0)}")
	multi = sum(1 for i in plan if i["record"].get("payroll_line_count", 0) > 1)
	print(f"  multi-company  : {multi}")

	if result["unresolved"]:
		print("\n=== unresolved reference data (would become errors) ===")
		for kind, values in sorted(result["unresolved"].items()):
			print(f"  {kind}: {len(values)}")
			for value in values[:12]:
				print(f"     - {value}")
			if len(values) > 12:
				print(f"     ... and {len(values) - 12} more")

	if result["warnings"]:
		print(f"\n=== warnings ({len(result['warnings'])}) ===")
		for warning in result["warnings"][:15]:
			print(f"  {warning}")
		if len(result["warnings"]) > 15:
			print(f"  ... and {len(result['warnings']) - 15} more")

	if result["errors"]:
		print(f"\n=== ERRORS ({len(result['errors'])}) - nothing was written ===")
		for error in result["errors"][:40]:
			print(f"  {error}")
		if len(result["errors"]) > 40:
			print(f"  ... and {len(result['errors']) - 40} more")
	else:
		print("\nno errors: the plan is ready to run")


def plan() -> None:
	"""Dry run.  Resolves and validates, writes nothing."""
	result = build_plan()
	report(result)
	frappe.db.rollback()


# ---------------------------------------------------------------------------
# write
# ---------------------------------------------------------------------------
def _employee_doc(item: dict) -> dict:
	record = item["record"]
	resolved = item["resolved"]
	doc = {
		"doctype": "Employee",
		"employee_number": record["employee_number"],
		"first_name": record["first_name"],
		"middle_name": record["middle_name"],
		"last_name": record["last_name"],
		"sdg_name_arabic": record["name_arabic"],
		"gender": GENDER.get(record.get("gender", ""), None),
		"date_of_birth": record.get("date_of_birth") or None,
		"date_of_joining": record.get("date_of_joining") or None,
		"status": item["status"],
		"company": resolved.get("company"),
		"designation": resolved.get("designation"),
		"department": resolved.get("department"),
		"branch": resolved.get("branch") or None,
		"sdg_governorate": record.get("sdg_governorate") or None,
		"personal_email": record.get("personal_email") or None,
		"company_email": record.get("company_email") or None,
		"current_address": record.get("current_address") or None,
		"sdg_job_profile": job_profile_html(record.get("job_profile", "")),
		"cell_number": first_phone(record.get("cell_number", "")) or None,
		# the master's cell packs relation, name and up to two numbers into one
		# string ("Brother-Anwar ( 01066007590 ) / 01007756947").  Writing it
		# straight into a Phone field fails validation, and the Employee hook
		# already knows how to split it, so it is handed over as-is.
		"_sdg_next_of_kin": record.get("emergency_phone_number") or "",
		"sdg_national_id": record["national_id"],
		"sdg_national_id_expiry": record.get("sdg_national_id_expiry") or None,
		"sdg_si_number": record.get("sdg_si_number") or None,
		"sdg_si_status": record.get("sdg_si_status") or None,
		"sdg_si_date": record.get("sdg_si_date") or None,
		"sdg_employment_category": resolved.get("employment_category"),
		"sdg_contract_type": resolved.get("contract_type"),
		"sdg_contract_date": record.get("contract_date") or None,
		"sdg_sub_department": resolved.get("sub_department"),
		"sdg_starting_designation": resolved.get("starting_designation"),
		"sdg_offer_status": record.get("offer_status") or None,
		"sdg_status_date": record.get("status_date") or None,
		"sdg_religion": record.get("religion") or None,
		"sdg_dependants_count": record.get("sdg_dependants_count") or 0,
		"sdg_military_status": record.get("military_status") or None,
		"sdg_military_document": record.get("military_document") or None,
		"sdg_is_person_of_determination": bool(record.get("sdg_is_person_of_determination")),
	}
	if record.get("marital_status"):
		doc["marital_status"] = record["marital_status"]
	# the EMPLOYEE FILE INDEX, one row per document
	doc["sdg_file_index"] = [
		{
			"document_item": row["document_item"],
			# a row the source left blank is written as an empty status rather
			# than defaulting to "Missing", which would assert a fact nobody
			# recorded
			"status": row["status"] or "",
			"notes": "",
		}
		for row in record.get("file_index", [])
	]
	if resolved.get("grade"):
		doc["grade"] = resolved["grade"]
	return doc


def run(limit: int | None = None) -> None:
	"""Write the plan.  Refuses to write anything if the plan has any error."""
	# Departments are a stock Link and none existed, so they are seeded first.
	# Seeding is idempotent, and the plan below re-validates afterwards, so a
	# partial seed can never let a bad record through.
	seed_departments()
	result = build_plan()

	if result["errors"]:
		report(result)
		frappe.throw(
			f"{len(result['errors'])} error(s) in the import plan; nothing was written. "
			"Fix the data or the fixture and run again.",
			title=frappe._("Employee import blocked"),
		)

	plan_list = result["plan"]
	if limit:
		plan_list = plan_list[:limit]

	# One transaction for the whole import.  Committing in batches meant a failure
	# on record 151 left 150 employees committed and no record of which - the exact
	# partial state the halt-on-error policy exists to prevent.
	done = 0
	for item in plan_list:
		doc = _employee_doc(item)
		if item["action"] == "update" and item["existing"]:
			save_employee(item["existing"], doc, ignore_mandatory=True)
		else:
			frappe.get_doc(doc).insert(ignore_mandatory=True)
		done += 1
		if done % 50 == 0:
			print(f"  ... {done}/{len(plan_list)}")

	frappe.db.commit()
	print(f"\nwrote {done} employees")
	print(f"  inserted: {sum(1 for i in plan_list if i['action'] == 'insert')}")
	print(f"  updated : {sum(1 for i in plan_list if i['action'] == 'update')}")
	frappe.clear_cache()
