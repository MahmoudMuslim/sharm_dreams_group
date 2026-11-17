# Copyright (c) 2026, Sharm Dreams Group and contributors
# For license information, please see license.txt

"""Guest-safe lookups for the public job application form.

HRMS ships a client script on the `job_application` Web Form that renders the
job title on the read-only "Job Opening" field. It does that by calling
`frappe.client.get_value`, which is declared `@frappe.whitelist()` without
`allow_guest=True`. An applicant reaching the form is `Guest`, and
`frappe.is_whitelisted` rejects the call before any permission check runs:

    You are not permitted to access this resource. Login to access
    Function frappe.client.get_value is not whitelisted.

That rejection is not a permission misconfiguration and granting `Guest` read
access on Job Opening does not help - `frappe.client.get_value` is rejected on
the method itself, not on the DocType behind it. `get_job_opening_title` is the
narrow replacement: guest-callable, and returning only the title of an opening
that is already published and still open.
"""

from __future__ import annotations

import frappe
from frappe import _


# `methods` must include POST: the web form client script reaches this through
# `frappe.call`, which POSTs by default. Restricting it to GET makes
# `is_valid_http_method` reject the real browser request with a bare
# "Not permitted" - the call is a read, so both verbs are appropriate.
@frappe.whitelist(allow_guest=True, methods=["GET", "POST"])
def get_job_opening_title(job_opening: str | None = None) -> dict:
	"""Return the title of a published, open Job Opening.

	Returns an empty dict for anything else - a closed opening, an unpublished
	one, or one that does not exist. The empty dict is what the client script
	already handles (`if (!r?.message) return;`), so an unpublished opening shows
	as a blank field rather than leaking a title.
	"""
	if not job_opening or not isinstance(job_opening, str):
		return {}

	title = frappe.db.get_value(
		"Job Opening",
		job_opening,
		["job_title", "status", "publish"],
		as_dict=True,
	)

	if not title or not title.publish or title.status != "Open":
		return {}

	return {"job_title": title.job_title or job_opening}


@frappe.whitelist(allow_guest=True, methods=["GET", "POST"])
def get_open_job_openings() -> list[dict]:
	"""List the published, open Job Openings, for a public careers page.

	Only the fields already rendered on the public template are returned - title,
	company, department, location, employment type and closing date. Nothing here
	is writable and nothing here identifies a person.
	"""
	if not frappe.db.exists("Website Settings"):
		frappe.throw(_("Website Settings not found"))

	return frappe.get_all(
		"Job Opening",
		filters={"publish": 1, "status": "Open"},
		fields=[
			"name",
			"job_title",
			"company",
			"department",
			"location",
			"employment_type",
			"closes_on",
			"vacancies",
		],
		order_by="posted_on desc, creation desc",
		limit=100,
	)
