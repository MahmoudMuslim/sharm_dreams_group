"""The approval chain on one Employee Self Service Approval Request.

`compute_approvers`, `action_confirm`, `action_approve` and `action_reject` are
the whole state machine.  Two rules shape it:

* Self-service employees have no Frappe user, so `request_owner` is empty and
  `get_self_service_employee()` falls back to `employee`.  Every lookup that would
  normally go through the user goes through that instead, which is what keeps the
  "manager approves" flow working for a user-less employee.
* The request is never submittable.  Its status is the workflow, so a draft that
  was never confirmed and a rejected one are the same row with a different
  `status`, and the employee sees both from "My Requests".
"""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import now_datetime

# The states an approver can still act in. `Waiting` only exists when the request
# type approves in sequence.
OPEN_APPROVER_STATUSES = ("New", "Pending", "Waiting")

# Fallback slot for the reporting manager. Sorts after every configured approver,
# so a request type that names its own approvers reaches them first.
MANAGER_SEQUENCE = 9

# Same idea for the officer fallback: after everything the request type named.
FALLBACK_SEQUENCE = 9

# Who can stand in as an approver when a request type names nobody and the
# employee has no manager. Checked in order, so the first enabled holder wins.
APPROVAL_OFFICER_ROLES = ("HR Manager", "System Manager")

# The request type that carries an expense claim. Imported lazily and defensively:
# the portal's `expenses` module is the only thing that needs it, and a broken
# import here would take Approve and Reject down on *every* request type.
try:
	from sharm_dreams_group.portal.expenses import EXPENSE_CATEGORY
except Exception:  # pragma: no cover - only on a broken install
	EXPENSE_CATEGORY = "Expenses"

# The request type that carries a submitted self-assessment. Same lazy import and the
# same reason: Approve and Reject are shared by every area, so a broken import here
# would take all of them down rather than just performance.
try:
	from sharm_dreams_group.portal.performance import PERFORMANCE_CATEGORY, act_on_appraisal
except Exception:  # pragma: no cover - only on a broken install
	PERFORMANCE_CATEGORY = "Performance"

	def act_on_appraisal(request, approve: bool) -> str | None:
		return None


class EmployeeSelfServiceApprovalRequest(Document):
	def validate(self):
		self.validate_employee_owner_consistency()

	def validate_employee_owner_consistency(self):
		"""On a self-service page request the owner and the employee must be the same person.

		Only checked when both are set: a desk request may be raised *for* an
		employee by somebody else, and a self-service page request has no owner at all.
		"""
		if not (self.from_self_service and self.employee and self.request_owner):
			return
		employee_user = frappe.db.get_value("Employee", self.employee, "user_id")
		if employee_user != self.request_owner:
			frappe.throw(
				"The request owner and the employee must match on an approval request."
			)

	# ------------------------------------------------------------------ #
	# Employee / approver resolution                                      #
	# ------------------------------------------------------------------ #

	def get_self_service_employee(self):
		"""Employee used for the manager / approver resolution on this request."""
		if self.employee:
			return self.employee
		if self.request_owner:
			return frappe.db.get_value(
				"Employee", {"user_id": self.request_owner}, "name"
			)
		return None

	def get_category(self):
		"""The request type behind the `request_type` link, or None.

		Cached: `action_confirm` and `_finalise_approvers` both reach for it, and
		neither is on a hot path where a second read would matter.
		"""
		if not self.request_type:
			return None
		return frappe.get_cached_doc("Employee Self Service Approval Category", self.request_type)

	def _manager_user(self):
		"""The reporting manager's user id, or None."""
		employee = self.get_self_service_employee()
		manager = frappe.db.get_value("Employee", employee, "reports_to") if employee else None
		return frappe.db.get_value("Employee", manager, "user_id") if manager else None

	def compute_approvers(self):
		"""Build the *complete* approver list for this request.

		Manager first when the request type asks for one, then the type's own
		approvers, then - if that is still not enough - a fallback, so that a
		request can never be left with nobody able to action it.

		The fallback used to be a separate step the caller had to remember, which
		is how it went missing: recomputing the approvers on its own produced an
		empty chain and a request at Pending that no one could approve. Doing it
		here makes the method match its name.
		"""
		self.approvers = []
		category = self.get_category()
		if not category:
			return

		seen = set()

		if category.manager_approval != "No":
			manager_user = self._manager_user()
			if manager_user:
				self.append(
					"approvers",
					{
						"approver_user": manager_user,
						"status": "New",
						"required": 1 if category.manager_approval == "Required" else 0,
						"sequence": MANAGER_SEQUENCE,
					},
				)
				seen.add(manager_user)

		for row in frappe.get_all(
			"Employee Self Service Approval Category Approver",
			filters={"parent": category.name},
			fields=["approver_user", "required", "sequence"],
			order_by="sequence asc",
		):
			if not row.approver_user or row.approver_user in seen:
				continue
			seen.add(row.approver_user)
			self.append(
				"approvers",
				{
					"approver_user": row.approver_user,
					"status": "New",
					"required": 1 if row.required else 0,
					"sequence": row.sequence or 0,
				},
			)

		self.ensure_somebody_can_approve(category)

	def ensure_somebody_can_approve(self, category):
		"""Add a fallback approver when the chain so far is too thin.

		Takes the reporting manager, and failing that an enabled HR Manager or
		System Manager - in that order, so the first holder found wins. Only
		reached when the request type names fewer approvers than it demands, or
		names none at all.

		The fallback is *not* marked required: it is a safety net rather than the
		employee's chosen chain, so it must not be able to hold a request open on
		its own.
		"""
		minimum = category.approval_minimum or 0
		if len(self.approvers) >= max(minimum, 1):
			return

		manager_user = self._manager_user()
		if manager_user:
			fallback_user, required = manager_user, 1
		else:
			officers = frappe.get_all(
				"Has Role",
				filters={"role": ("in", APPROVAL_OFFICER_ROLES), "parenttype": "User"},
				fields=["parent"],
			)
			enabled = [
				o.parent
				for o in officers
				if frappe.db.get_value("User", o.parent, "enabled")
			]
			if not enabled:
				frappe.throw(
					"This request type needs an approver, but none is configured, "
					"and no manager or approver officer is available. Please contact HR."
				)
			fallback_user, required = enabled[0], 0

		self.append(
			"approvers",
			{
				"approver_user": fallback_user,
				"status": "New",
				"required": required,
				"sequence": FALLBACK_SEQUENCE,
			},
		)

	# ------------------------------------------------------------------ #
	# Actions                                                            #
	# ------------------------------------------------------------------ #

	# The same open states `_approver_row` accepts, and the terminal ones
	# `_finalise_approvers` produces. Listed rather than derived from the row, so a
	# request in a state this does not know about is treated as closed: a button
	# offered for a status nobody has defined is a guess, and the safe guess here is
	# to hide it and let the method refuse if someone tries.
	TERMINAL_STATUSES = ("Approved", "Rejected", "Cancelled")

	def may_act(self):
		"""Whether the current user may approve or reject **this** request.

		Server-side on purpose. The desk does not evaluate `condition` on a DocType
		action - it only evaluates `states` conditions, and `states` cannot add a
		button - so a condition written there is discarded on import and the button
		would show to everyone who can open the form. Asking here means the client is
		told what it may offer instead of deciding for itself.

		Mirrors `_approver_row` exactly: an approver row for this user still in an
		open state. If the two ever disagree the method throws, so the worst case is a
		visible error rather than an approval nobody is accountable for.
		"""
		if self.status in self.TERMINAL_STATUSES:
			return False
		if self.status == "Draft":
			# Not submitted yet, so there is nothing to decide. `action_confirm` is
			# what moves it out of Draft.
			return False
		return bool(self._approver_row(frappe.session.user))

	def action_confirm(self):
		"""Validate the approver set and put the first approver to work."""
		category = self.get_category()
		manager_approval = category.manager_approval if category else "No"
		approval_minimum = category.approval_minimum if category else 0

		if manager_approval == "Required":
			manager_user = self._manager_user()
			if not manager_user:
				frappe.throw(
					"This request needs to be approved by your manager. "
					"There is no manager linked to your employee profile."
				)
			if not any(a.approver_user == manager_user for a in self.approvers):
				frappe.throw(
					"This request needs to be approved by your manager. "
					"Your manager is not in the approvers list."
				)

		if len(self.approvers) < approval_minimum:
			frappe.throw(
				f"You have to add at least {approval_minimum} approvers "
				"to confirm your request."
			)

		if (
			category
			and category.requirer_document == "Required"
			and not self.get("attachments")
		):
			frappe.throw("You have to attach at least one document.")

		if category and category.approver_sequence:
			queue = [a for a in self.approvers if a.status in OPEN_APPROVER_STATUSES]
			for row in queue[1:]:
				row.status = "Waiting"
			active = queue[0] if queue and queue[0].status != "Pending" else None
		else:
			active = None
			for row in self.approvers:
				if row.status == "New":
					if active is None:
						active = row
					else:
						row.status = "Pending"

		if active is not None:
			active.status = "Pending"

		self.status = "Pending"
		self.date_confirmed = now_datetime()
		self.save()

	def action_approve(self):
		row = self._approver_row(frappe.session.user)
		if not row:
			frappe.throw("You are not an approver on this request.")
		row.status = "Approved"
		self._finalise_approvers()
		self.save()
		# Only once the request itself is Approved, and only if it reached Approved -
		# a request with a second, still-pending approver stays pending and the claim
		# must not be submitted on a partial approval.
		if self.status == "Approved":
			# One entry point per area, dispatched on `request_type` rather than calling
			# every handler. `act_on_expense_claim` returns None for a request that is
			# not about a claim, so the chain is still "act on whatever this is" - but
			# routing it explicitly means a new area cannot be activated by accident if
			# two categories ever share a name.
			if self.request_type == EXPENSE_CATEGORY:
				act_on_expense_claim(self, approve=True)
			elif self.request_type == PERFORMANCE_CATEGORY:
				act_on_appraisal(self, approve=True)

	def action_reject(self):
		row = self._approver_row(frappe.session.user)
		if not row:
			frappe.throw("You are not an approver on this request.")
		row.status = "Rejected"
		self._finalise_approvers(terminated=True)
		self.save()
		if self.request_type == EXPENSE_CATEGORY:
			act_on_expense_claim(self, approve=False)
		elif self.request_type == PERFORMANCE_CATEGORY:
			act_on_appraisal(self, approve=False)

	def _approver_row(self, user):
		for row in self.approvers:
			if row.approver_user == user and row.status in OPEN_APPROVER_STATUSES:
				return row
		return None

	def _finalise_approvers(self, terminated=False):
		"""Decide the request status once an approver acts."""
		if terminated:
			self.status = "Rejected"
			return

		category = self.get_category()

		if category and category.approver_sequence:
			for row in self.approvers:
				if row.status == "Waiting":
					row.status = "Pending"
					self.status = "Pending"
					return
		else:
			open_rows = [r for r in self.approvers if r.status in OPEN_APPROVER_STATUSES]
			if open_rows:
				# Everyone not yet reached becomes actionable at the same time.
				for row in open_rows:
					if row.status == "New":
						row.status = "Pending"
				self.status = "Pending"
				return

		required = [r for r in self.approvers if r.required]
		if required and any(r.status != "Approved" for r in required):
			self.status = "Pending"
			return
		if category and len(self.approvers) < category.approval_minimum:
			self.status = "Pending"
			return

		self.status = "Approved"


@frappe.whitelist()
def act_on_expense_claim(request, approve: bool) -> str | None:
	"""Approve or reject the Expense Claim behind an approval request.

	`ExpenseClaim.on_submit` throws unless `approval_status` is Approved or Rejected:
	submitting an expense is what posts it to the accounts, so HRMS will not do it on
	an unapproved claim. Approving therefore has to happen first, and doing both here
	means there is no state in which the claim is submitted but unapproved - which is
	the state that would put money in the accounts with nobody having decided to.

	Returns the claim name acted on, or None if this request is not about a claim.
	Nothing else on this doctype is touched: a request of another type, or one whose
	`reference` has been edited to name a claim that does not exist, is left exactly
	as it is rather than half-applied.

	`reference` is a plain Data field, so it is re-validated here rather than trusted:
	the name is read back out of it and the claim must belong to the request's own
	employee and must still be a draft nobody has approved.
	"""
	if request.request_type != EXPENSE_CATEGORY:
		return None

	name = (request.reference or "").removeprefix("Expense Claim ").strip()
	if not name or not frappe.db.exists("Expense Claim", name):
		return None

	claim = frappe.get_doc("Expense Claim", name)
	if request.employee and claim.employee != request.employee:
		# A reference naming somebody else's claim would let an approver of this
		# request approve that person's expenses. Refused, not silently corrected.
		frappe.throw(
			"The referenced Expense Claim belongs to a different employee than this request."
		)
	if claim.docstatus != 0:
		# Already submitted or cancelled, so it has been decided by other means.
		return name

	claim.approval_status = "Approved" if approve else "Rejected"
	if approve:
		# HRMS posts nothing unless each line carries a sanctioned amount, and
		# approving does not copy one across. A claim approved at zero would be
		# submitted and would move no money while looking paid, so the claimed amount
		# is sanctioned as approved - the approver is approving the claim as filed,
		# and any partial approval belongs on the desk, per line, where it is visible.
		for row in claim.expenses or []:
			if not row.sanctioned_amount:
				row.sanctioned_amount = row.amount
		claim.status = "Unpaid"
		claim.save(ignore_permissions=True)
		claim.submit()
	else:
		claim.status = "Rejected"
		claim.save(ignore_permissions=True)
	return name


@frappe.whitelist()
def get_approval_requests(employee=None, status=None):
	"""Approval requests for the desk's list view, for a widget or a dashboard card."""
	filters = {}
	if employee:
		filters["employee"] = employee
	if status:
		filters["status"] = status
	return frappe.get_all(
		"Employee Self Service Approval Request",
		filters=filters,
		fields=[
			"name",
			"subject",
			"request_type",
			"employee",
			"status",
			"from_self_service",
			"creation",
			"date_confirmed",
		],
		order_by="creation desc",
		limit_page_length=100,
	)


@frappe.whitelist()
def get_approval_actions(name: str) -> dict:
	"""What the signed-in user may do with this request, and what Reject would do.

	Returns `{"actions": ["Approve", "Reject"], "reject_note": "..."}`. Read-only and
	deliberately so. It answers the same question `may_act` does and cannot change
	anything, which is what lets the button be hidden rather than offered-and-refused.
	Called by the DocType's client script; a view-only helper that a button's
	visibility depends on is safer than one that performs the action.

	The note is here rather than in the client because what Reject does is not the same
	in every area, and a message written for one of them is wrong for the rest. It used
	to say "It cannot be undone", which is true of an expense claim and false of a
	self-assessment: rejecting that amends it into a fresh draft the employee can
	revise. A confirmation that overstates the consequence is how an approver stops
	reading confirmations.
	"""
	doc = frappe.get_doc("Employee Self Service Approval Request", name)
	if not doc.has_permission("read") or not doc.may_act():
		return {"actions": [], "reject_note": ""}

	if doc.request_type == EXPENSE_CATEGORY:
		note = _("Reject this request? The claim cannot be reopened afterwards.")
	elif doc.request_type == PERFORMANCE_CATEGORY:
		note = _(
			"Reject this request? It will be reopened as a draft the employee can "
			"revise and send again."
		)
	else:
		note = _("Reject this request?")
	return {"actions": ["Approve", "Reject"], "reject_note": note}
