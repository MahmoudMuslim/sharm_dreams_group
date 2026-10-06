// Copyright (c) 2026, Sharm Dreams Group and contributors
// For license information, see license.txt

// Approve / Reject for a self-service approval request.
//
// The methods `action_approve` and `action_reject` already existed and already
// refuse anyone who is not an open approver on the request, by comparing
// `frappe.session.user` against the child rows. What was missing was any way for a
// human to call them: the DocType declared `"actions": []`, so the only caller in
// existence was the test suite.
//
// The DocType json declares the two buttons with no `condition`, because the desk
// does not evaluate `condition` on an action - only on `states`, which cannot add a
// button. So the gate is asked of the server (`get_approval_actions`, which reads
// the document's own `may_act`) and the buttons are added only when it says yes.
// There is no client-side copy of the approval rule to drift, and the methods still
// refuse if anyone calls them directly.
frappe.ui.form.on("Employee Self Service Approval Request", {
	refresh(frm) {
		if (frm.is_new() || frm.doc.status !== "Pending") return;

		frappe.call({
			method: "sharm_dreams_group.sharm_dreams_group.doctype."
				+ "employee_self_service_approval_request."
				+ "employee_self_service_approval_request.get_approval_actions",
			args: { name: frm.doc.name },
			callback(r) {
				// The server owns the wording as well as the gate: what Reject does is not
				// the same in every area, and a message written for one of them misleads
				// the approver about the others.
				const payload = (r && r.message) || {};
				const allowed = payload.actions || [];
				if (allowed.indexOf("Approve") === -1) return;
				const note = payload.reject_note || __("Reject this request?");

				frm.add_custom_button(
					__("Approve"),
					() => act(frm, "action_approve", "green", __("Approved")),
					__("Approve")
				);
				frm.add_custom_button(
					__("Reject"),
					() =>
						frappe.confirm(note, () =>
							act(frm, "action_reject", "orange", __("Rejected"))
						),
					__("Reject")
				);
			},
		});
	},
});

function act(frm, method, indicator, done_message) {
	frappe.call({
		method: "run_doc_method",
		args: {
			doctype: "Employee Self Service Approval Request",
			name: frm.doc.name,
			method: method,
		},
		freeze: true,
		callback() {
			frappe.show_alert({ message: done_message, indicator: indicator });
			frm.reload_doc();
		},
	});
}
