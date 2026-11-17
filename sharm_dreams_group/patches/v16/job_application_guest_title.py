# Copyright (c) 2026, Sharm Dreams Group and contributors
# For license information, please see license.txt

"""Point the job application web form at a guest-callable title lookup.

The HRMS `job_application` Web Form renders the job title client-side by calling
`frappe.client.get_value`, which is not guest-whitelisted, so every applicant
sees "Function frappe.client.get_value is not whitelisted" on the Apply page.
`sharm_dreams_group.hr.recruitment.get_job_opening_title` is the guest-safe
equivalent.

This has to be a patch rather than a one-off edit of the record: the Web Form is
`is_standard=1`, and `frappe.model.sync.sync_for` re-imports standard web forms
from the app on every `bench migrate`, which would restore the broken script.
"""

from __future__ import annotations

import frappe

WEB_FORM = "job-application"

OLD_CALL = 'method: "frappe.client.get_value"'

CLIENT_SCRIPT = """frappe.web_form.after_load = () => {
	const job_opening = frappe.utils.get_query_params().job_title;
	if (!job_opening) return;

	frappe.call({
		method: "sharm_dreams_group.hr.recruitment.get_job_opening_title",
		args: {
			job_opening: job_opening,
		},
		callback: (r) => {
			if (!r?.message) return;
			const title = frappe.utils.escape_html(r.message.job_title);
			$(frappe.web_form.fields_dict["job_title"].wrapper)
				.find(".control-value")
				.text(title);
		},
	});
};

frappe.web_form.on("resume_link", (field, value) => {
	if (!frappe.utils.is_url(value)) {
		frappe.msgprint(__("Resume link not valid"));
	}
});
"""


def execute():
	if not frappe.db.exists("Web Form", WEB_FORM):
		return

	web_form = frappe.get_doc("Web Form", WEB_FORM)
	if web_form.client_script and OLD_CALL not in web_form.client_script:
		# already moved off the broken call, or customised by hand - leave it alone
		return

	web_form.client_script = CLIENT_SCRIPT
	web_form.flags.ignore_permissions = True
	web_form.flags.ignore_mandatory = True
	web_form.save()

	frappe.clear_cache(doctype="Web Form")
