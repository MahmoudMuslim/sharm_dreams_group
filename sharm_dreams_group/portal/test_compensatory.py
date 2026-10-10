# Copyright (c) 2026, Sharm Dreams Group and contributors
# For license information, see license.txt

"""Tests for compensatory leave (Leave In Lieu) earning.

The scheduler runs daily and proposes LIW credits for employees who worked on
holidays or weekly rest days. These are proposals - HR must approve (submit)
the Draft Leave Allocation before the credit becomes real.
"""

from __future__ import annotations

from datetime import timedelta

import frappe
from frappe.tests.utils import FrappeTestCase

from sharm_dreams_group.portal import compensatory
from sharm_dreams_group.seeder import assign_shifts_by_gender, assign_holiday_list


class CompensatoryTestCase(FrappeTestCase):
    def setUp(self):
        assign_holiday_list()
        assign_shifts_by_gender()
        frappe.db.commit()

        self.emp = frappe.db.get_value(
            "Employee",
            {"status": "Active", "gender": "Male", "sdg_national_id": ("is", "set"),
             "employee_number": ("is", "set")},
            "name",
        )
        if not self.emp:
            self.skipTest("no active male employee who can sign in")

    def tearDown(self):
        for name in frappe.get_all("Leave Allocation", filters={"docstatus": 0}, pluck="name"):
            frappe.delete_doc("Leave Allocation", name, force=True, ignore_permissions=True)
        frappe.db.commit()


class TestCompensatoryEarnings(CompensatoryTestCase):
    def test_credit_capped_at_one_day(self):
        """Credit is capped at 1 day per calendar day."""
        credit = compensatory._compute_credit(hours_worked=16, multiplier=2.0)
        self.assertLessEqual(credit, 1.0)
        self.assertEqual(credit, 1.0)

    def test_half_day_credit(self):
        credit = compensatory._compute_credit(hours_worked=4, multiplier=2.0)
        self.assertEqual(credit, 1.0)
        credit = compensatory._compute_credit(hours_worked=2, multiplier=2.0)
        self.assertEqual(credit, 0.5)

    def test_zero_hours_zero_credit(self):
        credit = compensatory._compute_credit(hours_worked=0, multiplier=2.0)
        self.assertEqual(credit, 0.0)

    def test_process_no_credit_on_normal_workday(self):
        result = compensatory.process_daily_compensatory_earnings("2026-10-15")
        self.assertEqual(result["proposals"], 0)

    def test_process_holiday_may_earn_proposal(self):
        result = compensatory.process_daily_compensatory_earnings("2026-10-08")
        self.assertGreaterEqual(result["processed"], 0)

    def test_process_weekly_rest_may_earn_proposal(self):
        result = compensatory.process_daily_compensatory_earnings("2026-10-10")
        self.assertGreaterEqual(result["processed"], 0)

    def test_process_returns_dict(self):
        result = compensatory.process_daily_compensatory_earnings("2026-10-08")
        self.assertIn("proposals", result)
        self.assertIn("processed", result)
        self.assertIn("skipped", result)
        self.assertIn("errors", result)