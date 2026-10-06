# Copyright (c) 2026, Sharm Dreams Group and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document
from frappe.utils import getdate, nowdate


class SDGPenalty(Document):
	"""One recorded penalty.

	Two rules, both about the record rather than the person:

	1.  A penalty cannot be dated in the future.  It is the only field that can
	    be got wrong by a bulk paste, and a future penalty would sit in the total
	    before the thing it penalises has happened.
	2.  `points` defaults to 1, because a row saved from the grid without one
	    would otherwise read as a penalty worth nothing and quietly understate
	    the level.

	Deliberately does not touch salary.  The whole point is that it is recorded
	and acted on through the level, not deducted.
	"""

	def validate(self):
		if self.penalty_date and getdate(self.penalty_date) > getdate(nowdate()):
			frappe.throw(
				frappe._("A penalty cannot be dated in the future.").format(),
				title=frappe._("Check the penalty date"),
			)
		if self.points is None:
			self.points = 1
		if self.points < 0:
			frappe.throw(frappe._("Points cannot be negative."))
