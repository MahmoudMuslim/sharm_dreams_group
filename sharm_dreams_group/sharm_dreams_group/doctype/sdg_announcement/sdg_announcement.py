# Copyright (c) 2026, Sharm Dreams Group and contributors
# For license information, please see license.txt

from frappe.model.document import Document
import frappe


class SDGAnnouncement(Document):
	"""Company-wide announcements for the employee portal."""

	pass


def get_announcements_for_employee(employee_name: str) -> list:
	"""Get active announcements for a specific employee based on their company/department."""
	employee = frappe.get_doc("Employee", employee_name)
	today = frappe.utils.today()

	# Build filters
	filters = {
		"is_active": 1,
		"published_on": ["<=", today],
	}

	# Get all active announcements
	announcements = frappe.get_all(
		"SDG Announcement",
		filters=filters,
		fields=[
			"name", "title", "content", "priority", "published_on",
			"expires_on", "target_section", "target_companies", "target_departments"
		],
		order_by="priority desc, published_on desc",
		limit_page_length=50,
	)

	# Filter by target audience
	result = []
	for ann in announcements:
		if ann.expires_on and ann.expires_on < today:
			continue

		if ann.target_section == "Everyone":
			result.append(ann)
		elif ann.target_section == "By Company":
			target_companies = [c.company for c in ann.target_companies] if ann.target_companies else []
			if employee.company in target_companies:
				result.append(ann)
		elif ann.target_section == "By Department":
			target_departments = [d.department for d in ann.target_departments] if ann.target_departments else []
			if employee.department in target_departments:
				result.append(ann)

	return result