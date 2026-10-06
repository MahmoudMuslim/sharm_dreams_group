# Copyright (c) 2026, Sharm Dreams Group and contributors
# For license information, please see license.txt

from __future__ import annotations

import re

import frappe
from frappe.model.document import Document
from frappe.utils import flt

BAND_RE = re.compile(r"^(\d+)\s*k\s*([A-Za-z])$", re.IGNORECASE)


class SDGMedicalBand(Document):
	"""Coverage band issued by the medical insurer, e.g. `75k C` / `100k B` / `150k A`."""

	def validate(self):
		self.normalise_band()
		self.validate_band_format()

	def normalise_band(self):
		"""`150k a` and `150K A` both mean `150k A`."""
		raw = (self.band or "").strip()
		if not raw:
			return
		parts = raw.split(None, 1)
		if len(parts) == 2 and parts[1].strip():
			raw = f"{parts[0].strip().lower()} {parts[1].strip().upper()}"
		self.band = raw

	def validate_band_format(self):
		m = BAND_RE.match(self.band or "")
		if not m:
			frappe.throw(
				frappe._(
					"Band '{0}' does not match the expected form, for example '75k C' or '150k A'."
				).format(self.band),
				title=frappe._("Unrecognised coverage band"),
			)
		if not self.coverage_limit:
			self.coverage_limit = flt(m.group(1)) * 1000
		if flt(self.monthly_premium) < 0:
			frappe.throw(frappe._("Monthly premium cannot be negative."))
