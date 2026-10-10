# Copyright (c) 2026, Sharm Dreams Group and contributors
# For license information, see license.txt

"""The public careers pages: a published opening, and the page someone applies on.

Two halves of the same public surface, both reachable with no login at all.

**The Apply page is a guest page.** HRMS ships the `job-application` Web Form with
`login_required` off, so `/job_application/new?job_title=...` is visited signed out,
and so is every script the form runs. HRMS's own `client_script` resolves the Job
Opening behind that query parameter with `frappe.client.get_value` - a method with
no `allow_guest`, and one whose `Job Opening` is readable by `System Manager`,
`HR User` and `HR Manager` only. A guest therefore gets `You are not permitted to
access this resource. Login to access` before the form has painted, on a page that
is deliberately public.

`get_job_opening_title` is the replacement: the same answer (the human title of the
opening, which is not what its name is - names are `HR-OPN-2001`) with a check
that fits the page it serves. It answers only for a *published* opening, which is
the exact set `/jobs` already lists and the job pages already show to anyone, so it
widens nothing.

Screening questions are defined on the Job Opening as `application_questions` (a
Table field pointing to `Job Opening Question`). The script fetches them via the
guest-safe `get_application_questions` endpoint, renders them as dynamic fields,
and on submit serializes answers into a hidden field `screening_answers_json`.
A `Job Applicant` `after_insert` hook then expands that JSON into the
`screening_answers` child table.

The script that calls it is ours for the same reason: see
`apply_job_application_client_script`.

**The jobs list is filtered, not paginated by hand.** `/jobs` reads
`status == "Open" and publish == 1` straight from the table, so an opening that is
unpublished is not merely unlinked - it never reaches the list, the count, or the
filter sidebar.
"""

from __future__ import annotations

import frappe
import json

WEB_FORM = "job-application"

# HRMS's own script, minus the one call a guest cannot make. Everything else it
# does is kept: the title is written into the read-only `job_title` control rather
# than left as the document name, and `resume_link` is still checked to be a URL.
#
# The method is the app's own rather than `frappe.client.get_value` so that the
# page behaves the same signed in and signed out - a script that silently does
# nothing for a guest would show `HR-OPN-2001` where a person expects a job
# title, which is a quieter failure than an error and no more useful.
#
# The web form uses Vue.js for field management. Standard form submission does
# not include dynamically added fields. We intercept submit, collect all field
# values (via frappe.web_form.get_form_values), add screening answers, and
# submit via frappe.call to the standard web_form.accept endpoint.
APPLY_CLIENT_SCRIPT = """frappe.web_form.after_load = () => {
	const job_opening = frappe.utils.get_query_params().job_title;
	if (!job_opening) return;

	// 1. Resolve human title
	frappe.call({
		method: "sharm_dreams_group.hr.job_opening.get_job_opening_title",
		args: { name: job_opening },
		callback: (r) => {
			if (!r?.message) return;
			const title = frappe.utils.escape_html(r.message);
			$(frappe.web_form.fields_dict["job_title"].wrapper)
				.find(".control-value")
				.text(title);
		},
	});

	// 2. Fetch and render screening questions
	frappe.call({
		method: "sharm_dreams_group.hr.job_opening.get_application_questions",
		args: { job_opening: job_opening },
		callback: (r) => {
			if (!r?.message?.length) return;
			render_screening_questions(r.message);
		},
	});
};

function render_screening_questions(questions) {
	const container = $('<div class="screening-questions web-form-field">');
	container.append('<h4 class="text-muted mb-3">' + __("Screening Questions") + '</h4>');

	questions.forEach((q, idx) => {
		const wrapper = $('<div class="form-group mb-3">');
		const label = $('<label>').text(q.question + (q.reqd ? ' *' : ''));
		if (q.description) {
			label.append(' <small class="text-muted">(' + q.description + ')</small>');
		}
		wrapper.append(label);

		let input;
		switch (q.fieldtype) {
			case 'Select':
				input = $('<select class="form-control">');
				input.append('<option value="">' + __("Select") + '</option>');
				(q.options || '').split('\\n').forEach(opt => {
					const val = opt.trim();
					if (val) input.append('<option value="' + val + '">' + val + '</option>');
				});
				break;
			case 'Check':
				input = $('<div class="form-check">');
				const checkbox = $('<input type="checkbox" class="form-check-input">')
					.val('Yes');
				const checkLabel = $('<label class="form-check-label">').text(__("Yes"));
				input.append(checkbox).append(checkLabel);
				break;
			case 'Text':
				input = $('<textarea class="form-control" rows="3">');
				break;
			default:
				input = $('<input type="text" class="form-control">');
		}

		// Store question metadata for answer collection
		input.attr('data-screening-question', JSON.stringify({
			name: q.name,
			text: q.question,
			fieldtype: q.fieldtype,
			reqd: q.reqd
		}));
		wrapper.append(input);
		container.append(wrapper);
	});

	// Insert after the job_title field (or at end of form)
	const jobTitleWrapper = frappe.web_form.fields_dict["job_title"]?.wrapper;
	if (jobTitleWrapper) {
		$(jobTitleWrapper).after(container);
	} else {
		$('.web-form').append(container);
	}

	// 3. Override form submission to include screening answers
	const form = $('.web-form form').first();
	form.off('submit').on('submit', (e) => {
		e.preventDefault();
		e.stopPropagation();

		const btn = form.find('.submit-btn');
		const originalText = btn.text();
		btn.prop('disabled', true).text(__('Submitting...'));

		// Collect screening answers
		const screeningAnswers = [];
		let hasRequiredError = false;
		$('.screening-questions .form-group').each(function() {
			const input = $(this).find('input, select, textarea').first();
			const meta = input.data('screening-question');
			if (!meta) return;

			let value = input.val();
			if (input.is(':checkbox')) {
				value = input.is(':checked') ? 'Yes' : 'No';
			}

			if (meta.reqd && !value) {
				hasRequiredError = true;
				input.addClass('has-error');
			} else {
				input.removeClass('has-error');
			}

			screeningAnswers.push({
				question: meta.name,
				question_text: meta.text,
				answer: value || ''
			});
		});

		if (hasRequiredError) {
			frappe.msgprint({message: __("Please answer all required screening questions"), indicator: "red"});
			btn.prop('disabled', false).text(originalText);
			return;
		}

		// Get form values from Vue component
		let formValues = {};
		if (frappe.web_form.vue && typeof frappe.web_form.vue.get_form_values === 'function') {
			formValues = frappe.web_form.vue.get_form_values() || {};
		} else if (typeof frappe.web_form.get_form_values === 'function') {
			formValues = frappe.web_form.get_form_values() || {};
		} else {
			// Fallback: serialize form (won't capture Vue-managed fields reliably)
			formValues = form.serializeArray().reduce((acc, f) => { acc[f.name] = f.value; return acc; }, {});
		}

		// Add screening answers and submit via web_form.accept
		formValues.screening_answers_json = JSON.stringify(screeningAnswers);

		frappe.call({
			method: "frappe.website.doctype.web_form.web_form.accept",
			args: {
				web_form: "job-application",
				data: JSON.stringify(formValues)
			},
			callback: (r) => {
				btn.prop('disabled', false).text(originalText);
				if (r?.message) {
					// Success - redirect to success_url
					const successUrl = frappe.web_form.success_url || "/jobs";
					window.location.href = successUrl;
				} else if (r?.exc) {
					frappe.msgprint({message: r.exc, indicator: "red"});
				}
			},
			error: (xhr, status, error) => {
				btn.prop('disabled', false).text(originalText);
				frappe.msgprint({message: __("Submission failed. Please try again."), indicator: "red"});
			}
		});
	});
}

frappe.web_form.on("resume_link", (field, value) => {
	if (!frappe.utils.is_url(value)) {
		frappe.msgprint(__("Resume link not valid"));
	}
});
"""


@frappe.whitelist(allow_guest=True)
@frappe.read_only()
def get_job_opening_title(name: str | None = None) -> str | None:
	"""The title of a published Job Opening, for the public Apply page.

	Answers `None` rather than throwing for anything it will not describe: a guest
	typing a URL should not learn whether an unpublished opening exists, and a
	mistyped name is a missing title, not an error the page has to surface.
	"""
	if not name or not isinstance(name, str):
		return None

	return frappe.db.get_value("Job Opening", {"name": name, "publish": 1}, "job_title")


@frappe.whitelist(allow_guest=True)
@frappe.read_only()
def get_application_questions(job_opening: str | None = None) -> list[dict]:
	"""Return screening questions for a published Job Opening.

	Only returns questions for openings that are published (status Open, publish=1).
	Each question includes its name (for form field naming), label, type, options,
	required flag, and description.
	"""
	if not job_opening or not isinstance(job_opening, str):
		return []

	opening = frappe.get_doc("Job Opening", job_opening)
	if not opening or opening.status != "Open" or not opening.publish:
		return []

	return [
		{
			"name": q.name,
			"question": q.question,
			"fieldtype": q.fieldtype,
			"options": q.options,
			"reqd": q.reqd,
			"description": q.description,
		}
		for q in opening.application_questions
	]


def apply_job_application_client_script() -> str:
	"""Put the guest-safe Apply script back on the Web Form.

	HRMS ships that script as `hrms/hr/web_form/job_application/job_application.json`,
	and `frappe.model.sync.sync_all` imports every JSON under an app's module
	folders on each migrate - so an edit made to the document alone is reverted,
	silently, the next time the site is migrated. Running this from
	`setup.after_migrate` puts ours back *after* the sync has run, which is the
	only point in the cycle where the last word is available.

	Diffed rather than written unconditionally so a migrate with nothing to do
	leaves the document's `modified` alone.

	Returns one of "missing", "unchanged" or "changed".
	"""
	if not frappe.db.exists("Web Form", WEB_FORM):
		return "missing"

	if frappe.db.get_value("Web Form", WEB_FORM, "client_script") == APPLY_CLIENT_SCRIPT:
		return "unchanged"

	# `db.set_value` rather than a save: a Web Form is a website document with its
	# own validation around routes and fields, none of which a script-only edit
	# should be re-running during a migrate.
	frappe.db.set_value("Web Form", WEB_FORM, "client_script", APPLY_CLIENT_SCRIPT)
	frappe.clear_cache(doctype="Web Form")

	return "changed"


def process_screening_answers(doc, method=None) -> None:
	"""Expand screening_answers_json into the screening_answers child table.

	Runs on Job Applicant before_insert. The web form script serializes answers
	into the hidden `screening_answers_json` field. This hook parses that JSON
	and creates `Job Applicant Answer` rows, then clears the JSON field.
	"""
	json_text = doc.get("screening_answers_json")
	if not json_text:
		return

	try:
		answers = json.loads(json_text)
	except Exception:
		frappe.log_error("Failed to parse screening_answers_json", json_text)
		return

	if not isinstance(answers, list):
		return

	for ans in answers:
		if not isinstance(ans, dict):
			continue
		doc.append("screening_answers", {
			"question": ans.get("question"),
			"question_text": ans.get("question_text"),
			"answer": ans.get("answer"),
		})

	# Clear the JSON field to avoid storing redundant data
	doc.screening_answers_json = ""
