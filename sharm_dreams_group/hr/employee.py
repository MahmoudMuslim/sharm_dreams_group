# Copyright (c) 2026, Sharm Dreams Group and contributors
# For license information, please see license.txt

"""Employee-side rules for Sharm Dreams Group.

Registered in hooks.py as a `doc_events` hook on Employee.

Two jobs:

1.  Keep the derived tenure fields current.  `sdg_age`, `sdg_years_of_tenure`
    and `sdg_days_with_company` all come from `date_of_birth` / `date_of_joining`,
    but they are stored because HR reports on them.  Recomputing them here rather
    than only at import is what stops them going stale on the day someone edits a
    joining date by hand.

2.  Parse the master's next-of-kin cell.  That column packs a relationship, a
    name and one or two phone numbers into a single string, e.g.

        'Son - Mohamed Sabry ( 01016361630 )'
        'Brother- Yasser Mohamed ( 01551598835 )/ 01067442969'
        'Mohamed Samir ( 01005531303 )'      <- no relationship
        'Son - ( 01013015210 )'              <- no name
        '-'                                  <- nothing

    It is split across Employee's own `relation`, `person_to_be_contacted` and
    `emergency_phone_number`, because an emergency call needs the number, not the
    name of the relative.
"""

from __future__ import annotations

import calendar
import re

import frappe
from frappe.utils import getdate, nowdate

# The master's cell uses these relationship words, sometimes with no spaces
# around the dash ("Brother- Yasser").
RELATION_PATTERN = re.compile(
	r"\b(son|daughter|brother|sister|father|mother|husband|wife|spouse|child|"
	r"parent|brother-in-law|sister-in-law)\b",
	re.IGNORECASE,
)

# Egyptian mobile numbers, tolerating the spaces and dashes people type
PHONE_PATTERN = re.compile(r"(?:\+?20|0)?[12]\d{9}")

# A leading relation phrase, e.g. "Son - " or "Brother- "
LEADING_RELATION = re.compile(
	r"^\s*(son|daughter|brother|sister|father|mother|husband|wife|spouse|child|"
	r"parent|brother-in-law|sister-in-law)\s*[-:]?\s*",
	re.IGNORECASE,
)


def save_employee(employee_number: str, values: dict, ignore_mandatory: bool = False) -> None:
	"""Fetch, apply and save an Employee, retrying if the row moved underneath us.

	`Document.check_if_latest()` compares the `modified` timestamp captured when
	the document was read against the one in the database, and it is correct to
	do so.  A background search-index rebuild writes `modified` on the rows it
	indexes, so a bulk import can be overtaken in the window between reading a
	document and saving it.

	Disabling the check would let a genuine concurrent edit be silently
	overwritten, so instead the write is retried against a freshly read document.
	No rollback: the surrounding import is one transaction and must stay intact.
	"""
	from frappe.exceptions import TimestampMismatchError

	for attempt in range(3):
		doc = frappe.get_doc("Employee", employee_number)
		doc.update(values)
		doc.flags.ignore_mandatory = ignore_mandatory
		try:
			doc.save()
			return
		except TimestampMismatchError:
			if attempt == 2:
				raise
			print(f"  {employee_number}: changed by another process, retrying (attempt {attempt + 2})")


def validate_employee(doc) -> None:
	"""Recompute derived fields and normalise the next-of-kin cell."""
	compute_tenure(doc)
	normalise_next_of_kin(doc)


# ---------------------------------------------------------------------------
# derived tenure
# ---------------------------------------------------------------------------
def compute_tenure(doc) -> None:
	"""Set age, years of tenure and days with the company.

	Guarded on the field existing so this is safe on a site where the custom
	fields have not been created yet.
	"""
	fields = {f.fieldname for f in doc.meta.fields}

	today = getdate(nowdate())

	if "sdg_age" in fields and doc.date_of_birth:
		doc.sdg_age = age_on(doc.date_of_birth, today)
	if "sdg_days_with_company" in fields and doc.date_of_joining:
		delta = (today - getdate(doc.date_of_joining)).days
		doc.sdg_days_with_company = max(delta, 0)
	if "sdg_years_of_tenure" in fields and doc.date_of_joining:
		doc.sdg_years_of_tenure = round_years(doc.date_of_joining, today)


def age_on(date_of_birth, today) -> int:
	"""Completed years, so someone is not a year older on the day before."""
	dob = getdate(date_of_birth)
	years = today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))
	return max(years, 0)


def round_years(start, today, places: int = 2) -> float:
	"""Completed years plus the decimal remainder.

	The master shows 4-decimal figures like 29.4027, which is years plus a
	fraction.  Two places is enough for HR and keeps the field readable.

	A joining date in the future (contract signed before the start date) yields
	0.0 rather than a small negative number, which would otherwise reach the
	Employee form and any report that sorts on it.
	"""
	start = getdate(start)
	if start > today:
		return 0.0

	years = today.year - start.year - ((today.month, today.day) < (start.month, start.day))
	# The comparison above already removes the incomplete year, so years is the
	# completed count.  The anniversary is only needed to measure the fraction,
	# and has to be the most recent one on or before today.  An earlier version
	# also decremented years here, which double-counted the incomplete year and
	# made everyone with under a year of service read 0.0 instead of their
	# fraction - 16 employees joined between 2025-10 and 2025-12 and all hit it.
	anniversary = _anniversary(start, today.year)
	if anniversary > today:
		anniversary = _anniversary(start, today.year - 1)
	remainder = (today - anniversary).days / 365.25
	return round(max(years + max(remainder, 0.0), 0.0), places)


def _anniversary(start, year: int):
	"""The start date's month and day in `year`, clamped for 29 February.

	Someone who joined on 29 February has no anniversary in a non-leap year, and
	`getdate("2026-02-29")` raises.  They are treated as joining on the 28th, which
	is the usual convention.
	"""
	day = start.day
	if start.month == 2 and start.day == 29 and not calendar.isleap(year):
		day = 28
	return getdate(f"{year:04d}-{start.month:02d}-{day:02d}")


# ---------------------------------------------------------------------------
# next of kin
# ---------------------------------------------------------------------------
PLACEHOLDERS = {"-", "--", "---", "n/a", "na", "none", "nil", "no", "0", "?"}


def normalise_next_of_kin(doc) -> None:
	"""Split the packed next-of-kin string across the three Employee fields.

	Only fills a field that is empty, so a value typed by hand in the UI is never
	overwritten by a re-import.
	"""
	raw = (doc.get("_sdg_next_of_kin") or "").strip()
	if not raw or raw.lower() in PLACEHOLDERS:
		return

	relation = ""
	remainder = raw

	match = LEADING_RELATION.match(remainder)
	if match:
		relation = match.group(1).title()
		remainder = remainder[match.end() :]
	elif not RELATION_PATTERN.search(remainder):
		# no relationship word anywhere, so whatever follows is a name
		pass

	# phones first, while the parentheses still mark them
	phones = PHONE_PATTERN.findall(remainder)
	phone = phones[0] if phones else ""

	name = remainder
	if phones:
		name = PHONE_PATTERN.split(name)[0]
	name = name.strip(" ()-,:/").strip()
	name = re.sub(r"\s{2,}", " ", name)

	if not relation:
		found = RELATION_PATTERN.search(remainder)
		if found:
			relation = found.group(1).title()

	if relation and not doc.get("relation"):
		doc.relation = relation
	if name and not doc.get("person_to_be_contacted"):
		doc.person_to_be_contacted = name
	if phone and not doc.get("emergency_phone_number"):
		doc.emergency_phone_number = phone
