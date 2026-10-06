# Copyright (c) 2026, Sharm Dreams Group and contributors
# For license information, please see license.txt

from __future__ import annotations

from sharm_dreams_group.hr.vocabulary import SDGBaseVocabulary


class SDGSubDepartment(SDGBaseVocabulary):
	"""Third level of the org structure, used on Employee.sdg_sub_department.

	Seeded from the master's "Sub Department" column: 21 distinct values, one per
	employee.  `looks_like_a_location` is set where the master value is a place
	rather than a sub-department - the column conflates the two, and flagging it
	leaves the call to HR instead of hiding it in the import.
	"""
