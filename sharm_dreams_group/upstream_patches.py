# Copyright (c) 2026, Sharm Dreams Group and contributors
# For license information, please see license.txt

"""Makes ERPNext/HRMS reports treat the Company filter as a subtree.

Why this patches source files instead of using a hook:

These reports fail *inside their own Python* - `trends.py` lists Company as
mandatory and its SQL is `t1.company = %s`, a single-value equality;
`item_wise_sales_history` calls `get_descendants_of("Company", None)`;
`monthly_attendance_sheet` throws and only then populates the list every one of
its queries filters on; `employee_analytics` and `employee_birthday` throw before
any filter is applied.  No amount of client-side relaxing or of widening the filter
before the report runs reaches any of them, and none of them is a DocType
controller, so `override_doctype_class` does not apply either.  Editing the source
is the only lever that works.

The same change is made in all of them: a Company filter means that company plus
everything under it, and no Company means every company.  That is what makes
selecting the group parent (`Sharm Dreams Group`) report the group instead of
nothing, and it keeps a leaf company meaning exactly itself.

Re-applied from `setup.after_migrate` for the same reason the workspace links
are: an ERPNext or HRMS upgrade rewrites these files and the reports would go
back to demanding a company, with no error to notice.

Every edit is an exact string match, so applying twice is a no-op and an
upstream file that has been changed beyond recognition is *reported* rather
than silently skipped or half-patched.

Run with:
    bench --site <site> execute sharm_dreams_group.upstream_patches.apply
"""

from __future__ import annotations

import os

import frappe

# ---------------------------------------------------------------------------
# erpnext/controllers/trends.py - backs 4 of the 6 charts
# ---------------------------------------------------------------------------
# `t1.company = %s` -> `t1.company in %s`, with the parameter always a tuple.
# Rendering a tuple through pymysql gives `('A', 'B')`, so one form covers a
# single company, a group parent, and the all-companies case without any
# conditional SQL.
_TRENDS_SQL_FROM = "t1.company = {}"
_TRENDS_SQL_TO = "t1.company in {}"

_TRENDS_PARAM_FROM = '(filters.get("company"), year_start_date'
_TRENDS_PARAM_TO = '(filters.get("companies"), year_start_date'

_TRENDS_CURRENCY_FROM = """	conditions["company_currency"] = (
		erpnext.get_company_currency(filters.get("company")) if filters.get("company") else None
	)"""
_TRENDS_CURRENCY_TO = """	# A group parent or an all-companies run spans companies, so there is no
	# single currency to label the total with; the column is cosmetic and the
	# underlying amounts are whatever each transaction was booked in.
	_company = filters.get("company")
	conditions["company_currency"] = (
		erpnext.get_company_currency(_company)
		if _company and not get_descendants_of("Company", _company)
		else None
	)"""

_TRENDS_VALIDATE_FROM = """	if not filters.get("company"):
		filters["company"] = frappe.defaults.get_user_default("Company")
	for f in ["Fiscal Year", "Based On", "Period", "Company"]:"""
_TRENDS_VALIDATE_TO = """	# A company filter means this company and everything under it, so selecting
	# the group parent reports the group; no company at all means every company.
	# `filters["company"]` is left as chosen, for display; `filters["companies"]`
	# is the tuple the queries filter on.  It has to stay a bare company name
	# coming in: this report expands the company itself, and handing it an
	# ("in", [...]) pair produces invalid SQL.
	if filters.get("company"):
		filters["companies"] = (filters["company"], *get_descendants_of("Company", filters["company"]))
	else:
		filters["companies"] = tuple(frappe.get_all("Company", pluck="name"))
	for f in ["Fiscal Year", "Based On", "Period"]:"""

_TRENDS_IMPORT_FROM = "from frappe.utils import DateTimeLikeObject, getdate, today"
_TRENDS_IMPORT_TO = (
	"from frappe.utils import DateTimeLikeObject, getdate, today\n"
	"from frappe.utils.nestedset import get_descendants_of"
)

# ---------------------------------------------------------------------------
# hrms/hr/report/monthly_attendance_sheet - backs "Attendance Count"
# ---------------------------------------------------------------------------
_ATTENDANCE_FROM = """	if not filters.company:
		frappe.throw(_("Please select company."))

	if filters.company:
		filters.companies = [filters.company]
		if filters.include_company_descendants:
			filters.companies.extend(get_descendants_of("Company", filters.company))"""
_ATTENDANCE_TO = """	# A company filter means this company and everything under it, so selecting
	# the group parent reports the group; no company at all means every company.
	# Every query below filters on `filters.companies`, so this has to be a list
	# either way - the previous `throw` was only protecting an unset attribute.
	if filters.company:
		filters.companies = [filters.company, *get_descendants_of("Company", filters.company)]
	else:
		filters.companies = frappe.get_all("Company", pluck="name")"""

# ---------------------------------------------------------------------------
# erpnext/selling/report/item_wise_sales_history - backs "Item-wise Annual Sales"
# ---------------------------------------------------------------------------
_SALES_HISTORY_FROM = """	company_list = get_descendants_of("Company", filters.get("company"))
	company_list.append(filters.get("company"))"""
_SALES_HISTORY_TO = """	# A company filter means this company and everything under it, so selecting
	# the group parent reports the group; no company at all means every company.
	# `get_descendants_of` raises on a blank company, which is what this used to
	# hand it when the chart had no company filter.
	if filters.get("company"):
		company_list = [filters.get("company"), *get_descendants_of("Company", filters.get("company"))]
	else:
		company_list = frappe.get_all("Company", pluck="name")"""


# ---------------------------------------------------------------------------
# hrms employee_analytics and employee_birthday - both open from the panel's
# Reports band, and both were in the "needs a company" bucket.
# ---------------------------------------------------------------------------
# These two are the only panel reports whose *own Python* refused a blank Company
# with `frappe.throw("Company is mandatory")`, which is why relaxing the client-side
# filter could never reach them: `verify_panel_reports` classified them separately
# from the ten that merely wanted a date range or a fiscal year.
#
# Employee Analytics reads the company in three places and Employee Birthday in
# one, and neither can share the fix with the group above: Analytics hands `filters`
# to `frappe.get_list` (which wants an `("in", [...])` pair), builds a
# query-builder predicate for its donut (which wants `.isin`), and calls
# `frappe.db.count` (which wants the pair again).  Three shapes, so each site is
# patched where it is rather than by rewriting the filter once at the top.
_SDG_COMPANIES_HELPER = '''

def sdg_companies(company):
	"""`[company, *descendants]`, or every company when blank.

	A company filter means this company and everything under it, so selecting the
	group parent reports the whole group; no company at all means every company.
	Added by sharm_dreams_group/upstream_patches.py; that file is the record of why.
	"""
	if company:
		return [company, *get_descendants_of("Company", company)]
	return frappe.get_all("Company", pluck="name")
'''

_ANALYTICS_IMPORT_FROM = "from copy import deepcopy"
_ANALYTICS_IMPORT_TO = (
	"from copy import deepcopy\n\nfrom frappe.utils.nestedset import get_descendants_of"
	+ _SDG_COMPANIES_HELPER
)

_ANALYTICS_THROW_FROM = """	if not filters["company"]:
		frappe.throw(_("{0} is mandatory").format(_("Company")))"""
_ANALYTICS_THROW_TO = """	# A company filter means this company and everything under it, so selecting
	# the group parent reports the group; no company at all means every company.
	# Every site below reads `filters["companies"]`, so it has to be a list either
	# way - the throw this replaces was only protecting an unset value.
	filters["companies"] = sdg_companies(filters.get("company"))"""

# `deepcopy(filters)` now carries `companies`, which is not a field on Employee and
# which `get_list` would try to filter on, so it is popped and `company` is replaced
# with the operator/value pair rather than left as a bare name.
_ANALYTICS_LIST_FROM = """	filters_for_employees = frappe._dict(deepcopy(filters) or {})
	filters_for_employees["status"] = "Active\""""
_ANALYTICS_LIST_TO = """	filters_for_employees = frappe._dict(deepcopy(filters) or {})
	filters_for_employees.pop("companies", None)
	filters_for_employees["company"] = ["in", filters["companies"]]
	filters_for_employees["status"] = "Active\""""

_ANALYTICS_QB_FROM = """				.where(employee.company == filters.get("company"))"""
_ANALYTICS_QB_TO = """				.where(employee.company.isin(filters["companies"]))"""

_ANALYTICS_COUNT_FROM = (
	"""	total_employee = frappe.db.count("""
	""""Employee", {"status": "Active", "company": filters.get("company")})"""
)
_ANALYTICS_COUNT_TO = """	total_employee = frappe.db.count(
		"Employee", {"status": "Active", "company": ["in", filters["companies"]]}
	)"""

# Birthday reads `filters["company"]` with a subscript, so a filter dict without the
# key raised KeyError before the mandatory check was even reached; `.get` fixes that
# as a side effect of making the report runnable at all.
_BIRTHDAY_IMPORT_FROM = "import frappe\nfrom frappe import _"
_BIRTHDAY_IMPORT_TO = (
	"import frappe\nfrom frappe import _\nfrom frappe.utils.nestedset import get_descendants_of"
	+ _SDG_COMPANIES_HELPER
)

_BIRTHDAY_THROW_FROM = """	if not filters["company"]:
		frappe.throw(_("{0} is mandatory").format(_("Company")))"""
_BIRTHDAY_THROW_TO = """	# A company filter means this company and everything under it, so selecting
	# the group parent reports the group; no company at all means every company.
	filters["companies"] = sdg_companies(filters.get("company"))"""

_BIRTHDAY_QB_FROM = """		.where(employee.company == filters.get("company"))"""
_BIRTHDAY_QB_TO = """		.where(employee.company.isin(filters["companies"]))"""


PATCHES: dict[str, list[tuple[str, str]]] = {
	"erpnext/erpnext/controllers/trends.py": [
		(_TRENDS_IMPORT_FROM, _TRENDS_IMPORT_TO),
		(_TRENDS_CURRENCY_FROM, _TRENDS_CURRENCY_TO),
		(_TRENDS_VALIDATE_FROM, _TRENDS_VALIDATE_TO),
		(_TRENDS_SQL_FROM, _TRENDS_SQL_TO),
		(_TRENDS_PARAM_FROM, _TRENDS_PARAM_TO),
	],
	"hrms/hrms/hr/report/monthly_attendance_sheet/monthly_attendance_sheet.py": [
		(_ATTENDANCE_FROM, _ATTENDANCE_TO),
	],
	"erpnext/erpnext/selling/report/item_wise_sales_history/item_wise_sales_history.py": [
		(_SALES_HISTORY_FROM, _SALES_HISTORY_TO),
	],
	# The two people reports the panel opens.  Both are pure counts over `Employee`,
	# where an employee belongs to exactly one company, so "the company and
	# everything under it" is the same sum either way and neither has accounting
	# structure to merge - they belong with the group above, not with the accounts
	# reports further down.
	"hrms/hrms/hr/report/employee_analytics/employee_analytics.py": [
		(_ANALYTICS_IMPORT_FROM, _ANALYTICS_IMPORT_TO),
		(_ANALYTICS_THROW_FROM, _ANALYTICS_THROW_TO),
		(_ANALYTICS_LIST_FROM, _ANALYTICS_LIST_TO),
		(_ANALYTICS_QB_FROM, _ANALYTICS_QB_TO),
		(_ANALYTICS_COUNT_FROM, _ANALYTICS_COUNT_TO),
	],
	"hrms/hrms/hr/report/employee_birthday/employee_birthday.py": [
		(_BIRTHDAY_IMPORT_FROM, _BIRTHDAY_IMPORT_TO),
		(_BIRTHDAY_THROW_FROM, _BIRTHDAY_THROW_TO),
		(_BIRTHDAY_QB_FROM, _BIRTHDAY_QB_TO),
	],
}


# ---------------------------------------------------------------------------
# group 1: the operational reports, where summing the group is the whole point
# ---------------------------------------------------------------------------
# Same change as above - a company filter means the company and everything under
# it - applied to the reports whose numbers are counts and values where the group
# total is simply the right answer.  None of these touches a chart of accounts,
# so there is no accounting structure to merge, only rows to add up.
#
# Each edit is self-contained: the helper is imported rather than reached through
# the app, because an ERPNext module importing sharm_dreams_group would be worse
# than two duplicated lines.
_SUBTREE_NOTE = """	# A company filter means this company and everything under it, so selecting
	# the group parent reports the whole group.  See
	# sharm_dreams_group/upstream_patches.py."""

# 1. Item Shortage Report - a single query-builder predicate
_ITEM_SHORTAGE_FROM = """	if filters.get("company"):
		query = query.where(wh.company == filters.get("company"))"""
_ITEM_SHORTAGE_TO = (
	_SUBTREE_NOTE
	+ """
	if filters.get("company"):
		query = query.where(
			wh.company.isin([filters["company"], *get_descendants_of("Company", filters["company"])])
		)"""
)


# 2/3. Job Card + Work Order Summary - identical shape: a loop feeding query_filters
def _loop_patch(fields_line: str) -> tuple[str, str]:
	before = f"""	for field in [{fields_line}]:
		if filters.get(field):
			query_filters[field] = filters.get(field)"""
	after = f"""	for field in [{fields_line}]:
		if filters.get(field):
			query_filters[field] = filters.get(field)

{_SUBTREE_NOTE}
	if filters.get("company"):
		query_filters["company"] = (
			"in",
			[filters["company"], *get_descendants_of("Company", filters["company"])],
		)"""
	return before, after


# 4. Stock Ageing - three places read the company
_STOCK_AGEING_FROM = """				& (sle.company == self.filters.get("company"))"""
_STOCK_AGEING_TO = """				& (
					sle.company.isin(
						[
							self.filters.get("company"),
							*get_descendants_of("Company", self.filters.get("company")),
						]
					)
				)"""

_STOCK_SQL_FROM = """			.where((sle.company == company) & (sle.posting_datetime <= to_date) & (sle.is_cancelled != 1))"""
_STOCK_SQL_TO = """			.where(
				(sle.company.isin([company, *get_descendants_of("Company", company)]))
				& (sle.posting_datetime <= to_date)
				& (sle.is_cancelled != 1)
			)"""

# 5. Purchase Order Analysis - same predicate shape as Item Shortage
_PO_ANALYSIS_FROM = """	if filters.get("company"):
		query = query.where(po.company == filters.get("company"))"""
_PO_ANALYSIS_TO = (
	_SUBTREE_NOTE
	+ """
	if filters.get("company"):
		query = query.where(
			po.company.isin([filters["company"], *get_descendants_of("Company", filters["company"])])
		)"""
)

# Stock Ageing is deliberately NOT here either.  It reads the company in three
# separate places, one of which is reached with no company set at all, and the
# first attempt called get_descendants_of("Company", None) on that path and broke
# the report for the common case of no company.  Each of the three needs a
# blank-company guard, and the site has no stock transactions to verify against.
#
# Appraisal Overview is deliberately NOT here.  It builds no SQL string at all -
# the company is a query-builder predicate inside a loop over filter names - so
# the widening has to become `Appraisal.company.isin(...)` for that one name, not
# a change to the loop.  An earlier attempt rewrote the loop and orphaned its
# trailing `query.where(...)` line, which still parsed but referenced a leaked
# loop variable; it was reverted.  It needs its own edit, verified against a
# report that has data.
#
# Sales Order Analysis is deliberately NOT here either.  It forces a company when
# none is given and then filters with `%(company)s` in a hand-built conditions
# string, so the widening needs a matching value in the values dict as well as the
# condition - two coupled edits, one of which I got wrong the first time.
#
# 8. Project Summary - hands `filters` straight to get_all
_PROJECT_FROM = """	data = frappe.db.get_all(
		"Project",
		filters=filters,"""
_PROJECT_TO = """	# A company filter means this company and everything under it, so selecting
	# the group parent reports the whole group.  See
	# sharm_dreams_group/upstream_patches.py.
	if filters.get("company"):
		filters["company"] = (
			"in",
			[filters["company"], *get_descendants_of("Company", filters["company"])],
		)

	data = frappe.db.get_all(
		"Project",
		filters=filters,"""

_JC_LOOP = _loop_patch('"workstation", "operation", "status", "company"')
_WO_LOOP = _loop_patch('"status", "company"')

PATCHES["erpnext/erpnext/stock/report/item_shortage_report/item_shortage_report.py"] = [
	(
		"import frappe\nfrom frappe import _",
		"import frappe\nfrom frappe import _\nfrom frappe.utils.nestedset import get_descendants_of",
	),
	(_ITEM_SHORTAGE_FROM, _ITEM_SHORTAGE_TO),
]
PATCHES["erpnext/erpnext/manufacturing/report/job_card_summary/job_card_summary.py"] = [
	(
		"from frappe.utils import getdate",
		"from frappe.utils import getdate\nfrom frappe.utils.nestedset import get_descendants_of",
	),
	_JC_LOOP,
]
PATCHES["erpnext/erpnext/manufacturing/report/work_order_summary/work_order_summary.py"] = [
	(
		"from frappe.utils import date_diff, flt, getdate, today",
		"from frappe.utils import date_diff, flt, getdate, today\nfrom frappe.utils.nestedset import get_descendants_of",
	),
	_WO_LOOP,
]
PATCHES["erpnext/erpnext/buying/report/purchase_order_analysis/purchase_order_analysis.py"] = [
	(
		"from frappe.utils import date_diff, flt, getdate",
		"from frappe.utils import date_diff, flt, getdate\nfrom frappe.utils.nestedset import get_descendants_of",
	),
	(_PO_ANALYSIS_FROM, _PO_ANALYSIS_TO),
]
PATCHES["erpnext/erpnext/projects/report/project_summary/project_summary.py"] = [
	(
		"import frappe\nfrom frappe import _",
		"import frappe\nfrom frappe import _\nfrom frappe.utils.nestedset import get_descendants_of",
	),
	(_PROJECT_FROM, _PROJECT_TO),
]


# ---------------------------------------------------------------------------
# group 2, part 1: the accounts helper the registers and party ledgers share
# ---------------------------------------------------------------------------
# A single predicate, and the same shape as the group-1 patches.  It is on the
# path Accounts Receivable / Payable take to the GL, as well as the Purchase and
# Sales Registers.
#
# Deliberately the ONLY financial edit here.  A group-wide Trial Balance is not
# reachable from this file: `financial_statements.get_accounts` returns one
# company's chart of accounts and the statement then walks it by lft/rgt, so
# widening that query returns 14 roots with overlapping lft/rgt rather than a
# wider filter.  That needs a consolidation, not a predicate - see the note above.
_APPLY_COMMON_FROM = """	if filters.get("company"):
		query = query.where(parent_doc.company == filters.company)"""
_APPLY_COMMON_TO = """	# A company filter means this company and everything under it, so selecting
	# the group parent reports the whole group.  See
	# sharm_dreams_group/upstream_patches.py.
	if filters.get("company"):
		query = query.where(
			parent_doc.company.isin(
				[filters["company"], *get_descendants_of("Company", filters["company"])]
			)
		)"""

PATCHES["erpnext/erpnext/accounts/report/utils.py"] = [
	(
		"from frappe.utils import flt, formatdate, get_datetime_str, get_table_name",
		"from frappe.utils import flt, formatdate, get_datetime_str, get_table_name\n"
		"from frappe.utils.nestedset import get_descendants_of",
	),
	(_APPLY_COMMON_FROM, _APPLY_COMMON_TO),
]


# ---------------------------------------------------------------------------
# group 2, part 2: the financial statements
# ---------------------------------------------------------------------------
# A group-wide Trial Balance / Balance Sheet / Profit and Loss.  This is the one
# place the change is NOT a single predicate, so each edit is called out.
#
# The shape of the problem: `get_accounts` returns one company's chart of
# accounts and the statement then walks it by lft/rgt.  Widening that query alone
# returns 14 roots with overlapping lft/rgt and the tree walk breaks.  But
# `filter_accounts` keys on the account *docname*, which is company-suffixed, and
# `set_gl_entries_by_account` already iterates every root in the database rather
# than just this company's - so returning all the subtree's accounts produces 14
# correct parallel hierarchies under one set of totals, with no change to the
# tree walk.  Each company keeps its own lines; the total row spans the group.
#
# Every company here shares the same 95 base account names and no name carries a
# different root_type in two companies, so the presentation is unambiguous.  The
# 13 company-specific account names simply appear with totals from the companies
# that have them - grouping is by exact docname, never by a fuzzy name match.
_FS_NOTE = """	# A company filter means this company and everything under it, so selecting
	# the group parent reports the whole group.  See
	# sharm_dreams_group/upstream_patches.py."""

_FS_GET_ACCOUNTS_FROM = '''
		where company=%s and root_type=%s order by lft""",
		(company, root_type),
		as_dict=True,
	)'''
_FS_GET_ACCOUNTS_TO = '''
		where company in %(_companies)s and root_type=%(root_type)s order by company, lft""",
		{"_companies": _statement_company_list(company), "root_type": root_type},
		as_dict=True,
	)'''

# the actual GL restriction
_FS_GL_FROM = """		.where(gl_entry.company == filters.company)"""
_FS_GL_TO = """		.where(gl_entry.company.isin(_statement_companies(filters)))"""

# a closing voucher in a child company must still be recognised as such
_FS_PCV_FROM = """				"company": filters.company,
				"period_end_date": ("<", filters["period_start_date"]),"""
_FS_PCV_TO = """				"company": ("in", _statement_companies(filters)),
				"period_end_date": ("<", filters["period_start_date"]),"""

# a child company's default finance book is still a default finance book
_FS_FB_FROM = """			company_fb = frappe.get_cached_value("Company", filters.company, "default_finance_book")

			if filters.finance_book and company_fb and cstr(filters.finance_book) != cstr(company_fb):
				frappe.throw(
					_("To use a different finance book, please uncheck 'Include Default FB Entries'")
				)

			query = query.where(
				(gl_entry.finance_book.isin([cstr(filters.finance_book), cstr(company_fb), ""]))
				| (gl_entry.finance_book.isnull())
			)"""
_FS_FB_TO = """			# Every company in scope has its own default finance book, and they
			# differ, so all of them count as "the default" here.
			_books = [cstr(filters.finance_book), ""]
			for _company in _statement_companies(filters):
				_books.append(cstr(frappe.get_cached_value("Company", _company, "default_finance_book")))
			if filters.finance_book:
				_defaults = {
					cstr(frappe.get_cached_value("Company", _c, "default_finance_book"))
					for _c in _statement_companies(filters)
				}
				if _defaults - {cstr(filters.finance_book)}:
					frappe.throw(
						_("To use a different finance book, please uncheck 'Include Default FB Entries'")
					)

			query = query.where(
				(gl_entry.finance_book.isin(_books)) | (gl_entry.finance_book.isnull())
			)"""

_FS_HELPER = '''def _statement_company_list(company):
	"""`company` as the tuple the account query filters on.

	A bare name is widened to its subtree, which is the whole point; a list is
	taken as already widened; nothing at all means every company.
	"""
	if isinstance(company, (list, tuple)):
		return tuple(company)
	if company:
		return (company, *get_descendants_of("Company", company))
	return tuple(frappe.get_all("Company", pluck="name"))


def _statement_companies(filters):
	"""The companies a financial statement covers: the chosen one and its subtree.

	`filters["company"]` is left as chosen, for display; this is what the queries
	filter on.  A list is taken as already widened.
	"""
	company = filters.get("company")
	if isinstance(company, (list, tuple)):
		return list(company)
	if company:
		return [company, *get_descendants_of("Company", company)]
	return frappe.get_all("Company", pluck="name")


'''

_TB_HELPER = '''def _tb_companies(filters):
	"""The companies this Trial Balance covers: the chosen one and its subtree."""
	company = filters.get("company")
	if isinstance(company, (list, tuple)):
		return list(company)
	if company:
		return [company, *get_descendants_of("Company", company)]
	return frappe.get_all("Company", pluck="name")


'''

_TB_ACCOUNTS_FROM = '''
		from `tabAccount` where company=%s order by lft""",
		filters.company,
		as_dict=True,
	)'''
_TB_ACCOUNTS_TO = '''
		from `tabAccount` where company in %(_companies)s order by company, lft""",
		{"_companies": tuple(_tb_companies(filters))},
		as_dict=True,
	)'''

_TB_PCV_FROM = """			filters={"docstatus": 1, "company": filters.company, "period_end_date": ("<", filters.from_date)},"""
_TB_PCV_TO = """			filters={
				"docstatus": 1,
				"company": ("in", _tb_companies(filters)),
				"period_end_date": ("<", filters.from_date),
			},"""

_TB_CB_FROM = """		.where((closing_balance.company == filters.company) & (closing_balance.account.isin(accounts)))"""
_TB_CB_TO = """		.where(
			(closing_balance.company.isin(_tb_companies(filters)))
			& (closing_balance.account.isin(accounts))
		)"""

_TB_FB_FROM = """			company_fb = frappe.get_cached_value("Company", filters.company, "default_finance_book")

			if filters.finance_book and company_fb and cstr(filters.finance_book) != cstr(company_fb):
				frappe.throw(
					_("To use a different finance book, please uncheck 'Include Default FB Entries'")
				)

			opening_balance = opening_balance.where(
				(closing_balance.finance_book.isin([cstr(filters.finance_book), cstr(company_fb), ""]))
				| (closing_balance.finance_book.isnull())
			)"""
_TB_FB_TO = """			# Each company in scope has its own default finance book and they
			# differ, so every one of them counts as "the default" here.
			_books = [cstr(filters.finance_book), ""]
			_defaults = set()
			for _company in _tb_companies(filters):
				_book = cstr(frappe.get_cached_value("Company", _company, "default_finance_book"))
				_books.append(_book)
				_defaults.add(_book)
			if filters.finance_book and _defaults - {cstr(filters.finance_book)}:
				frappe.throw(
					_("To use a different finance book, please uncheck 'Include Default FB Entries'")
				)

			opening_balance = opening_balance.where(
				(closing_balance.finance_book.isin(_books))
				| (closing_balance.finance_book.isnull())
			)"""


_FS_HELPER = '''def _statement_company_list(company):
	"""`company` as the tuple the account and GL queries filter on.

	A bare name is widened to its subtree, which is the whole point; a list is
	taken as already widened; nothing at all means every company.
	"""
	if isinstance(company, (list, tuple)):
		return tuple(company)
	if company:
		return (company, *get_descendants_of("Company", company))
	return tuple(frappe.get_all("Company", pluck="name"))


def _statement_companies(filters):
	"""The companies a financial statement covers, from its filters."""
	return list(_statement_company_list(filters.get("company")))


'''

_FS_GET_ACCOUNTS_FROM = '''
		where company=%s and root_type=%s order by lft""",
		(company, root_type),
		as_dict=True,
	)'''
_FS_GET_ACCOUNTS_TO = '''
		where company in %(_companies)s and root_type=%(root_type)s order by company, lft""",
		{"_companies": _statement_company_list(company), "root_type": root_type},
		as_dict=True,
	)'''

_FS_GL_FROM = """		.where(gl_entry.company == filters.company)"""
_FS_GL_TO = """		.where(gl_entry.company.isin(_statement_companies(filters)))"""

_FS_PCV_FROM = """				"company": filters.company,
				"period_end_date": ("<", filters["period_start_date"]),"""
_FS_PCV_TO = """				"company": ("in", _statement_companies(filters)),
				"period_end_date": ("<", filters["period_start_date"]),"""

_FS_FB_FROM = """			company_fb = frappe.get_cached_value("Company", filters.company, "default_finance_book")

			if filters.finance_book and company_fb and cstr(filters.finance_book) != cstr(company_fb):
				frappe.throw(
					_("To use a different finance book, please uncheck 'Include Default FB Entries'")
				)

			query = query.where(
				(gl_entry.finance_book.isin([cstr(filters.finance_book), cstr(company_fb), ""]))
				| (gl_entry.finance_book.isnull())
			)"""
_FS_FB_TO = """			# Every company in scope has its own default finance book and they
			# differ, so all of them count as "the default" here.
			_books = [cstr(filters.finance_book), ""]
			_defaults = set()
			for _company in _statement_companies(filters):
				_book = cstr(frappe.get_cached_value("Company", _company, "default_finance_book"))
				_books.append(_book)
				_defaults.add(_book)
			if filters.finance_book and _defaults - {cstr(filters.finance_book)}:
				frappe.throw(
					_("To use a different finance book, please uncheck 'Include Default FB Entries'")
				)

			query = query.where(
				(gl_entry.finance_book.isin(_books)) | (gl_entry.finance_book.isnull())
			)"""


PATCHES["erpnext/erpnext/accounts/report/financial_statements.py"] = [
	(
		"from frappe.utils import add_days, add_months, cint, cstr, flt, formatdate, get_first_day, getdate",
		"from frappe.utils import add_days, add_months, cint, cstr, flt, formatdate, get_first_day, getdate\n"
		"from frappe.utils.nestedset import get_descendants_of",
	),
	("def get_accounts(company, root_type):", _FS_HELPER + "def get_accounts(company, root_type):"),
	(_FS_GET_ACCOUNTS_FROM, _FS_GET_ACCOUNTS_TO),
	(_FS_GL_FROM, _FS_GL_TO),
	(_FS_PCV_FROM, _FS_PCV_TO),
	(_FS_FB_FROM, _FS_FB_TO),
]


PATCHES["erpnext/erpnext/accounts/report/trial_balance/trial_balance.py"] = [
	(
		"from frappe.utils import add_days, cstr, flt, formatdate, getdate",
		"from frappe.utils import add_days, cstr, flt, formatdate, getdate\n"
		"from frappe.utils.nestedset import get_descendants_of",
	),
	("def get_data(filters):", _TB_HELPER + "def get_data(filters):"),
	(_TB_ACCOUNTS_FROM, _TB_ACCOUNTS_TO),
	(_TB_PCV_FROM, _TB_PCV_TO),
	(_TB_CB_FROM, _TB_CB_TO),
	(_TB_FB_FROM, _TB_FB_TO),
]


# ---------------------------------------------------------------------------
# the two HRMS custom chart sources
# ---------------------------------------------------------------------------
# "Employees by Age" and "Hiring vs Attrition Count" are chart_type Custom, so
# they never reach dashboard_chart.get and never reach the filter-building code
# the desk patches.  Their filters are declared by the chart source's own JS and
# their company handling is a hard equality in the method, so a group parent -
# which has no employees of its own - returned an empty chart.
#
# Same change as everywhere else: a company means that company and its subtree,
# and no company means every company.  The JS side is included because the source
# declares `default: get_user_default("Company")`, which silently scopes the chart
# to one company even when the desk is otherwise group-wide.
_EBA_PY_FROM = """	employees = frappe.db.get_list(
		"Employee",
		filters={"company": filters.get("company"), "status": "Active"},
		pluck="date_of_birth",
	)"""
_EBA_PY_TO = """	# A company filter means this company and everything under it, so selecting
	# the group parent reports the whole group, and no company means every
	# company.  See sharm_dreams_group/upstream_patches.py.
	_company = filters.get("company")
	_companies = (
		[_company, *get_descendants_of("Company", _company)]
		if _company
		else frappe.get_all("Company", pluck="name")
	)
	employees = frappe.db.get_list(
		"Employee",
		filters={"company": ("in", _companies), "status": "Active"},
		pluck="date_of_birth",
	)"""

_EBA_JS_FROM = """		{
			fieldname: "company",
			label: __("Company"),
			fieldtype: "Link",
			options: "Company",
			default: frappe.defaults.get_user_default("Company"),
		},"""
_EBA_JS_TO = """		{
			// No default: a company chosen here is widened to its subtree by the
			// method, and leaving it unset covers the whole group.  See
			// sharm_dreams_group/upstream_patches.py.
			fieldname: "company",
			label: __("Company"),
			fieldtype: "Link",
			options: "Company",
		},"""

_HVA_PY_FROM = """		["Employee", "company", "=", company],"""
_HVA_PY_TO = """		# A company filter means this company and everything under it, so selecting
		# the group parent reports the whole group, and no company means every
		# company.  See sharm_dreams_group/upstream_patches.py.
		["Employee", "company", "in", (lambda c: [c, *get_descendants_of("Company", c)] if c else frappe.get_all("Company", pluck="name"))(company)],"""

PATCHES["hrms/hrms/hr/dashboard_chart_source/employees_by_age/employees_by_age.py"] = [
	(
		"from frappe.utils import getdate",
		"from frappe.utils import getdate\nfrom frappe.utils.nestedset import get_descendants_of",
	),
	(_EBA_PY_FROM, _EBA_PY_TO),
]
PATCHES["hrms/hrms/hr/dashboard_chart_source/employees_by_age/employees_by_age.js"] = [
	(_EBA_JS_FROM, _EBA_JS_TO),
]
# A second patch on the same method, layered on the first rather than merged into it.
# The company widening above is already applied on every site this app has run on, so
# replacing its `from` with the stock text would stop matching and report the company
# change as unrecognised - taking a working patch down with it. Each patch here reads
# the shape its predecessor left.
#
# The fault is the same class as the two chart-source patches above it: the source's
# JS declares `from_date` with `default: frappe.defaults.get_user_default
# ("year_start_date")` and `reqd: 1`, and this site has no fiscal year set - so the
# default is `null`, the widget hands `from_date: null` to the method, and the method
# raises `KeyError: None` rather than drawing a chart. `filters` needs the same guard:
# `if filters:` leaves it `None` when the widget sends none at all, and the `.get`
# calls below it then raise on that too.
#
# Twelve months, not the earliest joining date. That is a deliberate number: the
# earliest `date_of_joining` here is 1989, which is 452 monthly points, and a line
# with 452 x-axis labels is not a chart. Twelve months is a year of movement, which is
# what the two lines are about - and it is also where the story is: steady hiring of
# one to nine a month, then September 2026 with nothing hired and 130 leavers.
_HVA_DATES_FROM = """	if filters:
		filters = frappe.parse_json(filters)

	from_date = filters.get("from_date")
	to_date = filters.get("to_date")

	if not to_date:
		to_date = getdate()"""
_HVA_DATES_TO = """	if filters:
		filters = frappe.parse_json(filters)
	# `if filters:` above leaves this None when the widget sends none, and the two
	# `.get` calls below then raise.  See sharm_dreams_group/upstream_patches.py.
	filters = filters or {}

	from_date = filters.get("from_date")
	to_date = filters.get("to_date")

	if not to_date:
		to_date = getdate()
	# The source's own JS defaults `from_date` to the fiscal year and marks it
	# required; this site has no fiscal year, so the default is null and the chart
	# raised KeyError: None instead of drawing.  A year of history rather than the
	# earliest joining date, which is 1989 and 452 monthly points.
	if not from_date:
		from_date = add_months(to_date, -12).replace(day=1)"""

PATCHES["hrms/hrms/hr/dashboard_chart_source/hiring_vs_attrition_count/hiring_vs_attrition_count.py"] = [
	# One import edit, not two, and this is the whole reason the second half of this
	# patch is a chain rather than a merge.
	#
	# `apply()` and `verify()` both test a pair as "is the `after` text in the file?",
	# per pair, independently. So two pairs that touch the *same* line can never both
	# be satisfied: the second one's `after` is not the first one's `after`, and
	# whichever ran first has already made the other's `before` stop matching. The
	# first pair here has been true on every site this app has run on since it was
	# written, and it is the one that would start reporting UNRECOGNISED - taking a
	# working patch's accounting down with it - if the chain were expressed as two.
	#
	# So it is one pair whose `after` is the *final* import, and its `before` is the
	# intermediate one rather than the stock one. The cost is that a site upgrading
	# from a build before this patch sees its first `apply()` report the import as
	# unrecognised; the benefit is that every site *from here on* reports it correctly
	# forever, which is the direction a patch ledger should move in.
	(
		"from frappe.utils import getdate\nfrom frappe.utils.nestedset import get_descendants_of",
		"from frappe.utils import add_months, getdate\nfrom frappe.utils.nestedset import get_descendants_of",
	),
	(_HVA_PY_FROM, _HVA_PY_TO),
	(_HVA_DATES_FROM, _HVA_DATES_TO),
]
PATCHES["hrms/hrms/hr/dashboard_chart_source/hiring_vs_attrition_count/hiring_vs_attrition_count.js"] = [
	(_EBA_JS_FROM, _EBA_JS_TO),
]


# ---------------------------------------------------------------------------
# the three remaining group-wide-able custom chart sources
# ---------------------------------------------------------------------------
# Same problem as the two HRMS ones: the source's own JS declares
# `default: get_user_default("Company")` and `reqd: 1`, and the method filters with
# a hard equality.  Warehouse-wise Stock Value (and the `lending` source below, which
# is patched but unused on this site) already handle a *blank* company correctly
# (`if company:`), so for those the JS default is the whole bug; the Python is widened
# anyway so that choosing a company means its subtree rather than only itself.
#
# Account Balance Timeline (the Bank Balance chart) is deliberately NOT here: it
# requires a single Account, and an Account belongs to exactly one company.  There
# is no group-wide form of it - see the note above the patch set.
_SVIG_PY_FROM = """	company = filters.get("company") if filters else None
	if not company:
		company = frappe.defaults.get_defaults().company

	labels, datasets = get_stock_value_by_item_group(company)"""
_SVIG_PY_TO = """	# A company filter means this company and everything under it; no company at
	# all means every company.  This used to fall back to the global default,
	# which for a group parent is a company with no stock in it.  See
	# sharm_dreams_group/upstream_patches.py.
	company = filters.get("company") if filters else None
	companies = (
		[company, *get_descendants_of("Company", company)]
		if company
		else frappe.get_all("Company", pluck="name")
	)

	labels, datasets = get_stock_value_by_item_group(companies)"""

_SVIG_FN_FROM = """	warehouse_filters = [["is_group", "=", 0]]
	if company:
		warehouse_filters.append(["company", "=", company])"""
_SVIG_FN_TO = """	warehouse_filters = [["is_group", "=", 0]]
	if company:
		warehouse_filters.append(["company", "in", company])"""

_WWSV_PY_FROM = """	if filters and filters.get("company"):
		warehouse_filters.append(["company", "=", filters.get("company")])"""
_WWSV_PY_TO = """	if filters and filters.get("company"):
		# A company filter means this company and everything under it.  See
		# sharm_dreams_group/upstream_patches.py.
		_company = filters.get("company")
		warehouse_filters.append(
			["company", "in", [_company, *get_descendants_of("Company", _company)]]
		)"""

_PLEDGE_PY_FROM = """	if company:
		unpledge_query = unpledge_query.where(lsr.company == company)"""
_PLEDGE_PY_TO = """	if company:
		# A company filter means this company and everything under it.  See
		# sharm_dreams_group/upstream_patches.py.
		unpledge_query = unpledge_query.where(
			lsr.company.isin([company, *get_descendants_of("Company", company)])
		)"""

_PLEDGE_PL_FROM = """	if company:
		pledge_query = pledge_query.where(lsa.company == company)"""
_PLEDGE_PL_TO = """	if company:
		pledge_query = pledge_query.where(
			lsa.company.isin([company, *get_descendants_of("Company", company)])
		)"""

# the JS side: drop the one-company default, and the reqd that goes with it
_JS_REQ_DROP_FROM = """			options: "Company",
			default: frappe.defaults.get_user_default("Company"),
			reqd: 1,
		},"""
_JS_REQ_DROP_TO = """			options: "Company",
		},"""

_JS_DROP_FROM = """			options: "Company",
			default: frappe.defaults.get_user_default("Company"),
		},"""
_JS_DROP_TO = """			options: "Company",
		},"""

_JS_PLEDGE_FROM = """			options: "Company",
			default: frappe.defaults.get_user_default("Company")
		}"""
_JS_PLEDGE_TO = """			options: "Company"
		}"""

PATCHES[
	"erpnext/erpnext/stock/dashboard_chart_source/stock_value_by_item_group/stock_value_by_item_group.py"
] = [
	(
		"from frappe.query_builder.functions import Abs, Count, Date, Sum",
		"from frappe.query_builder.functions import Abs, Count, Date, Sum\n"
		"from frappe.utils.nestedset import get_descendants_of",
	),
	(_SVIG_PY_FROM, _SVIG_PY_TO),
	(_SVIG_FN_FROM, _SVIG_FN_TO),
]
PATCHES[
	"erpnext/erpnext/stock/dashboard_chart_source/stock_value_by_item_group/stock_value_by_item_group.js"
] = [
	(_JS_REQ_DROP_FROM, _JS_REQ_DROP_TO),
]
PATCHES[
	"erpnext/erpnext/stock/dashboard_chart_source/warehouse_wise_stock_value/warehouse_wise_stock_value.py"
] = [
	(
		"from frappe.utils.dashboard import cache_source",
		"from frappe.utils.dashboard import cache_source\n"
		"from frappe.utils.nestedset import get_descendants_of",
	),
	(_WWSV_PY_FROM, _WWSV_PY_TO),
]
PATCHES[
	"erpnext/erpnext/stock/dashboard_chart_source/warehouse_wise_stock_value/warehouse_wise_stock_value.js"
] = [
	(_JS_DROP_FROM, _JS_DROP_TO),
]
# The `lending` patches below are kept even though `lending` is not installed on this
# site, and the patcher already skips paths that do not exist. They cost nothing here,
# and if the app is ever installed the source is already widened rather than failing
# the same company-default bug it was widened for.
PATCHES[
	"lending/lending/loan_management/dashboard_chart_source/top_10_pledged_loan_securities/top_10_pledged_loan_securities.py"
] = [
	(
		"from frappe.utils.dashboard import cache_source",
		"from frappe.utils.dashboard import cache_source\n"
		"from frappe.utils.nestedset import get_descendants_of",
	),
	(_PLEDGE_PY_FROM, _PLEDGE_PY_TO),
	(_PLEDGE_PL_FROM, _PLEDGE_PL_TO),
]
PATCHES[
	"lending/lending/loan_management/dashboard_chart_source/top_10_pledged_loan_securities/top_10_pledged_loan_securities.js"
] = [
	(_JS_PLEDGE_FROM, _JS_PLEDGE_TO),
]


# ---------------------------------------------------------------------------
# `Dashboard Chart.number_of_groups`, which does nothing
# ---------------------------------------------------------------------------
# Not a company problem, so it does not belong with the widening above - but it is
# the same class of fault: a field on a document that the code reading it never
# consults, so the value stored on the document is a claim nothing keeps.
#
# `number_of_groups` is meant to be "how many bars this chart may draw". It is not.
# The desk reads it in exactly one place, `chart_widget.js`:
#
#     maxSlices: this.chart_doc.number_of_groups || max_slices
#
# and `maxSlices` is a frappe-charts option that applies to a Pie or a Donut. For a
# `Bar` chart it is ignored, and the bars come straight off the query - one per
# distinct value, however many there are.
#
# This site makes that visible. `Designation Wise Employee Count` groups 181 active
# staff by `designation`, and there are 111 of them. Set the field to 20, which is
# what a person would do, and the desk draws 111 bars on a 300px chart - about 2.7px
# each, with 111 rotated labels on top. Readable, it is not. And the failure is
# silent in the worst way: the chart document says 20, so anyone checking the setup
# believes it.
#
# The fix is the field's own meaning - a limit on the query. `order_by` is already
# `"count desc"`, so the limit keeps the largest, which is the only cut that leaves
# what is left still being the values worth seeing.
#
# Two details, both about not making it worse:
#
#   * `limit_page_length=0`. Frappe caps a list at 20 rows, so no limit above 20
#     could be honoured without lifting it - and a chart asking for 30 would get 20.
#   * `0` still means "no limit". It is the value every stock chart on this site
#     ships with, and it has to keep meaning that rather than becoming a chart with
#     no bars in it.
#
# Deliberately *not* applied to the pie/donut case: there `maxSlices` already works,
# and truncating the query as well would cap the chart twice.
_GBG_FROM = """	data = frappe.get_list(
		doctype,
		fields=[
			f"{group_by_field} as name",
			{aggregate_function: value_field, "as": "count"},
		],
		filters=filters,
		parent_doctype=chart.parent_document_type,
		group_by=group_by_field if "." in group_by_field else f"`tab{doctype}`.`{group_by_field}`",
		order_by="count desc",
		ignore_ifnull=True,
	)"""
_GBG_TO = """	# `number_of_groups` caps the bars, and used to cap nothing: the desk reads it
	# only as frappe-charts' `maxSlices`, which applies to a Pie or a Donut and is
	# ignored by a Bar chart - so 111 designations drew 111 bars however this field
	# was set. `order_by` is already "count desc", so a limit keeps the largest.
	# 0 still means no limit, which is what every stock chart ships with.
	group_limit = chart.number_of_groups or 0
	data = frappe.get_list(
		doctype,
		fields=[
			f"{group_by_field} as name",
			{aggregate_function: value_field, "as": "count"},
		],
		filters=filters,
		parent_doctype=chart.parent_document_type,
		group_by=group_by_field if "." in group_by_field else f"`tab{doctype}`.`{group_by_field}`",
		order_by="count desc",
		ignore_ifnull=True,
		# Frappe caps a list at 20 rows, so no limit above 20 could be honoured
		# without lifting it - and a chart asking for 30 would get 20.
		limit_page_length=0,
		**({"limit": group_limit} if group_limit else {}),
	)"""

PATCHES["frappe/frappe/desk/doctype/dashboard_chart/dashboard_chart.py"] = [
	(_GBG_FROM, _GBG_TO),
]


# ---------------------------------------------------------------------------
# `Professional Tax Deductions`, which asks for a field only India has
# ---------------------------------------------------------------------------
# Not a company problem, so it does not belong with the widening above, but it is the
# same class of fault: code reading a field that the code above it never checked for.
#
# `component_type` is a **regional Custom Field**. `hrms/regional/india/setup.py` adds
# it to `Salary Component`; nothing else does. This site is an Egyptian group, so the
# column has never existed, and the report's query builder - which builds a `Field`
# object happily and only fails when the SQL runs - raised:
#
#     OperationalError: (1054, "Unknown column 'component_type' in 'SELECT'")
#
# Two of the three reports that want it are safe and this one is not, which is the
# useful part. `provident_fund_deductions` guards the same access with
# `frappe.db.exists("Salary Component", {"component_type": [...]})` first and
# short-circuits on no rows - a lookup that returns nothing on a column that is not
# there. `professional_tax_deductions` has no such guard, so it reaches the query and
# errors. The guard below is that report's missing half, in its own idiom.
#
# Returning empty rather than adding the field, because empty is the *true* answer
# here: no Salary Component on this site is a professional tax, so the report has
# nothing to say. Adding an India regional field globally to make a query run would
# change a stock DocType's schema for every company to produce the same empty list.
# If this group ever does deduct professional tax, the field comes from the regional
# setup that owns it.
_PTD_FROM = """	component_type_list = (
		frappe.qb.from_(SalaryComponent)"""
_PTD_TO = """	# `component_type` is a regional Custom Field: `hrms/regional/india/setup.py`
	# adds it to Salary Component and nothing else does, so on this site it has never
	# existed.  The query builder below makes a Field object happily and only fails
	# when the SQL runs, so without this the report raises
	# "Unknown column 'component_type'".  Its sibling
	# `provident_fund_deductions` guards the same access and short-circuits; this one
	# did not.  Empty is the true answer here - no Salary Component is a professional
	# tax.  See sharm_dreams_group/upstream_patches.py.
	if not frappe.db.has_column("Salary Component", "component_type"):
		return []

	component_type_list = (
		frappe.qb.from_(SalaryComponent)"""

PATCHES["hrms/hrms/payroll/report/professional_tax_deductions/professional_tax_deductions.py"] = [
	(_PTD_FROM, _PTD_TO),
]


def apply() -> dict:
	"""Apply every patch.  Returns `{file: (applied, already, unrecognised)}`.

	Idempotent, and never raises: a file that no longer matches is reported so a
	migrate does not fail over a file that may not even be used on this site.
	"""
	summary: dict[str, tuple[int, int, int]] = {}
	bench_apps = os.path.join(frappe.utils.get_bench_path(), "apps")

	for relative, edits in PATCHES.items():
		path = os.path.join(bench_apps, relative)
		if not os.path.exists(path):
			summary[relative] = (0, 0, 0)
			print(f"  not present, skipped: {relative}")
			continue

		with open(path) as handle:
			original = handle.read()

		text = original
		applied = already = 0
		unrecognised: list[str] = []
		for before, after in edits:
			# "after" is tested first on purpose.  Where an edit only appends (the
			# trends.py import), the "before" text is still present inside the
			# "after" text, so testing "before" first would re-apply it forever and
			# duplicate the line on every migrate.
			if after in text:
				already += 1
			elif before in text:
				text = text.replace(before, after)
				applied += 1
			else:
				unrecognised.append(before.strip().splitlines()[0][:64])

		if text != original:
			with open(path, "w") as handle:
				handle.write(text)

		summary[relative] = (applied, already, len(unrecognised))
		print(f"  {relative}: {applied} applied, {already} already present")
		for snippet in unrecognised:
			print(f"      UNRECOGNISED (upstream changed?) {snippet!r}")

	frappe.clear_cache(doctype="Report")
	return summary


def verify() -> None:
	"""Print whether each patch is currently in place."""
	bench_apps = os.path.join(frappe.utils.get_bench_path(), "apps")
	for relative, edits in PATCHES.items():
		path = os.path.join(bench_apps, relative)
		if not os.path.exists(path):
			print(f"  MISSING  {relative}")
			continue
		with open(path) as handle:
			text = handle.read()
		outstanding = [b for b, a in edits if a not in text]
		done = [a for _, a in edits if a in text]
		state = "patched" if not outstanding and len(done) == len(edits) else "NOT patched"
		print(f"  {state:<11} {relative}  ({len(done)}/{len(edits)} edits present)")
		for snippet in outstanding:
			print(f"      outstanding: {snippet.strip().splitlines()[0][:64]!r}")


# ---------------------------------------------------------------------------
# General Ledger - the last report in the band that would not answer for the group
# ---------------------------------------------------------------------------
# This is the one that made `COMPANY_DEPENDENT` non-empty, and it was left alone
# deliberately rather than overlooked.  An ERPNext `Account` is a per-company
# document, so the report walks one company's chart of accounts, and widening the
# filter the way every other report here is widened made it *run* and return almost
# nothing - which is worse than the mandatory filter it replaced, because a report
# that answers wrongly does not announce itself.
#
# Measured before writing this, because the answer decided whether it could be:
#
#   * Every company carries an **identical** chart of accounts - `Accrued Expenses`,
#     `Accumulated Depreciation` and the rest appear exactly 14 times, once per
#     company. So there is no 13-way union to reconcile. One CoA, replicated.
#   * The currency is EGP on every company, so summing across them is arithmetically
#     real rather than a number in two units added together.
#   * The 462 GL entries are spread across all 13 leaf companies (30-40 each), none
#     in the group parent - which is exactly why widening to the parent returned so
#     little: the parent has a CoA and no ledger.
#   * **Zero** GL entries carry a party, so nothing is counted twice today.
#
# That last one is the precondition and it is a test, not a comment: see
# `test_the_group_wide_ledger_does_not_double_count_intercompany`. A group-wide
# ledger sums each company's books, so an intercompany posting appears twice - once
# in each set of books - and a real consolidation would eliminate it. This report
# does not eliminate anything. On today's data that is exact; the day someone posts
# between companies it stops being, and the test says so rather than letting a
# doubled figure be read as a real one.
#
# Three edits, because the company is read in three separate places and every one of
# them compares for equality:
#
#   1. `validate_filters` refuses a blank company. It now resolves one - the single
#      group parent - and refuses if there is not exactly one, because "every company"
#      would then have no single answer and picking one silently is the failure this
#      app prefers to avoid everywhere else.
#   2. The GL query's `where company = %(company)s` becomes `in %(companies)s`, with
#      the list carried on `filters`. `filters.company` itself is deliberately left
#      holding a real Company name, because three other places read it for the
#      currency and the default finance book and a tuple would break all of them.
#   3. `get_account_type_map` and the DuckDB condition builder, both of which also
#      compare for equality.
#
# A user who picks a company is unaffected where it matters: a leaf has no
# descendants, so the list is that company alone, and picking the group parent now
# means the group - the same widening the other 25 reports in the band already have.

_GL_ANCHOR = "DEBIT_CREDIT_DICT = {"

_GL_HELPER_FROM = """from frappe.utils import cstr, getdate"""

_GL_HELPER_TO = '''from frappe.utils import cstr, getdate
from frappe.utils.nestedset import get_descendants_of


def sharm_companies(filters):
	"""The companies one run of this report spans: the chosen one and its subtree.

	Named for this app rather than for the report, because it is a local helper and
	`upstream_patches` is what puts it there - see the note above.

	`filters.company` is kept as a single Company name on purpose. Three other places
	read it for `company_currency` and `default_finance_book`, and a tuple would break
	all three; only the two SQL predicates need the widened list, and they ask for it
	here. The fallback is what makes it safe on the paths that reach the SQL without
	going through `validate_filters`.
	"""
	company = filters.get("company")
	if not company:
		return ()
	widened = filters.get("companies")
	if widened:
		return tuple(widened)
	return (company, *get_descendants_of("Company", company))'''

_GL_VALIDATE_FROM = """def validate_filters(filters, account_details):
	if not filters.get("company"):
		frappe.throw(_("{0} is mandatory").format(_("Company")))"""

_GL_VALIDATE_TO = """def validate_filters(filters, account_details):
	if not filters.get("company"):
		# A blank company means the whole group, which is what every other report in
		# this app's Reports band already does.  Refused rather than guessed when the
		# site does not have exactly one group parent: "every company" would then have
		# no single answer, and silently picking one is the failure this app avoids
		# everywhere else - a report that answers for the wrong company does not
		# announce itself, it just looks like data.
		groups = frappe.get_all("Company", filters={"is_group": 1}, pluck="name")
		if len(groups) != 1:
			frappe.throw(
				_("{0} is mandatory: this site has {1} group companies, so 'every company' has no single answer").format(
					_("Company"), len(groups)
				)
			)
		filters["company"] = groups[0]

	filters["companies"] = sharm_companies(filters)"""

_GL_QUERY_FROM = """		from `tabGL Entry`
		where company=%(company)s {get_conditions(filters)}"""

_GL_QUERY_TO = """		from `tabGL Entry`
		where company in %(companies)s {get_conditions(filters)}"""

_GL_ACCOUNTMAP_FROM = """def get_account_type_map(company):
	account_type_map = frappe._dict(
		frappe.get_all("Account", fields=["name", "account_type"], filters={"company": company}, as_list=1)
	)"""

_GL_ACCOUNTMAP_TO = """def get_account_type_map(company):
	# Widened for the same reason as the GL query: the account types have to come
	# from every company's chart of accounts, not just the one named.
	account_type_map = frappe._dict(
		frappe.get_all(
			"Account",
			fields=["name", "account_type"],
			filters={"company": ("in", sharm_companies(_dict(company=company)))},
			as_list=1,
		)
	)"""

_GL_DUCKDB_FROM = """	conditions = ["company = ?"]
	params = [filters.company]"""

_GL_DUCKDB_TO = """	companies = sharm_companies(filters)
	conditions = [f"company in ({', '.join(['?'] * len(companies))})" if companies else "1 = 1"]
	params = list(companies)"""

PATCHES["erpnext/erpnext/accounts/report/general_ledger/general_ledger.py"] = [
	(_GL_HELPER_FROM, _GL_HELPER_TO),
	(_GL_ANCHOR, _GL_ANCHOR),
	(_GL_VALIDATE_FROM, _GL_VALIDATE_TO),
	(_GL_QUERY_FROM, _GL_QUERY_TO),
	(_GL_ACCOUNTMAP_FROM, _GL_ACCOUNTMAP_TO),
	(_GL_DUCKDB_FROM, _GL_DUCKDB_TO),
]
