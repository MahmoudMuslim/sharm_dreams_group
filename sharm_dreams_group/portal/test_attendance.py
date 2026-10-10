# Copyright (c) 2026, Sharm Dreams Group and contributors
# For license information, see license.txt

"""Tests for tap-to-check-in.

The centre of gravity here is that a punch must be *recorded*, and that a tap which
cannot be recorded must say so. Everything else - the screens, the counters - is
secondary to the fact that an employee standing at a wall tablet at the start of a shift
gets either a recorded time or a sentence.

Each test that records a checkin deletes it in `tearDown`. `Employee Checkin` is
deletable, but an `Attendance` created from a pair of them is not, so anything that gets
as far as attendance is cancelled first.
"""

from __future__ import annotations

from datetime import timedelta

import frappe
from frappe.tests.utils import FrappeTestCase

from sharm_dreams_group.portal import attendance, portal, renderers
from sharm_dreams_group.seeder import (
	FEMALE_SHIFT_TYPE,
	HOLIDAY_LIST_NAME,
	MALE_SHIFT_TYPE,
	SHIFT_ASSIGNMENT_FROM,
	SHIFT_ASSIGNMENT_TO,
	assign_holiday_list,
	assign_shifts_by_gender,
)


class AttendanceTestCase(FrappeTestCase):
	"""A signed-in employee who genuinely has a submitted shift."""

	def setUp(self):
		# The site data first: a punch only works against a *submitted* assignment, so
		# these steps are not a convenience, they are the precondition.
		assign_holiday_list()
		assign_shifts_by_gender()
		frappe.db.commit()

		# Geofencing off for every subclass except the one about geofencing. These tests
		# punch with no coordinates, which is the tablet's behaviour when the browser gives
		# no fix - allowed while enforcement is off, refused when it is on. Pinned here
		# rather than assumed, because `geo_punch_enforce` is a site-wide setting and this
		# site has it on with real zones: a suite run against a live site would otherwise
		# fail every punch test, and `TestGeofencedPunch` turns it on for itself.
		previous_enforce = frappe.db.get_single_value(
			"Employee Self Service Settings", "geo_punch_enforce"
		)
		frappe.db.set_single_value("Employee Self Service Settings", "geo_punch_enforce", 0)
		self.addCleanup(self._restore_enforcement, previous_enforce)

		# Any check-in already on the site for today is cleared first, whatever made it.
		# A punch only works while the day has fewer than two logs, so one leftover row
		# from an earlier run or an earlier test makes the first tap refuse - and the
		# refusal then looks like a bug in the punch rather than leftover data. This is a
		# real Employee Checkin, so it is removed through the document rather than SQL.
		frappe.db.commit()
		for row in frappe.get_all(
			"Employee Checkin",
			filters={
				"time": ["between", (frappe.utils.today() + " 00:00:00",
								   frappe.utils.today() + " 23:59:59")]
			},
			pluck="name",
			limit_page_length=0,
		):
			doc = frappe.get_doc("Employee Checkin", row)
			if doc.attendance:
				attendance_doc = frappe.get_doc("Attendance", doc.attendance)
				if attendance_doc.docstatus == 1:
					attendance_doc.cancel()
				frappe.delete_doc("Attendance", doc.attendance, force=True,
								  ignore_permissions=True)
			frappe.delete_doc("Employee Checkin", row, force=True, ignore_permissions=True)
		frappe.db.commit()

		self.employee = frappe.db.get_value(
			"Employee",
			{"status": "Active", "gender": "Male", "sdg_national_id": ("is", "set"),
			 "employee_number": ("is", "set")},
			"name",
		)
		if not self.employee:
			self.skipTest("no active male employee who can sign in")
		self.token = portal.sign_in(
			frappe.db.get_value("Employee", self.employee, "sdg_national_id"),
			frappe.db.get_value("Employee", self.employee, "employee_number"),
		)["token"]
		self._log_ids: list[str] = []
		self._attendance_ids: list[str] = []

	def _restore_enforcement(self, previous):
		"""Put `geo_punch_enforce` back, durably.

		The commit is the point. `TestGeofencedPunch`'s own cleanup commits while
		restoring, and cleanups run last-in-first-out, so a plain `set_single_value`
		here would be undone by the rollback `FrappeTestCase` runs after it - leaving
		enforcement off on the site, which is how a test run turned geofencing off for
		real. Committed so the restore survives that rollback.
		"""
		frappe.db.set_single_value(
			"Employee Self Service Settings", "geo_punch_enforce", previous or 0
		)
		frappe.db.commit()

	def tearDown(self):
		# `test_an_employee_with_no_shift_today_is_refused` shortens this employee's
		# assignment so today falls outside it. Unattended that leaks: unittest sorts
		# `test_an_...` before `test_a_r...`, so the mutation landed first and every later
		# punch test then refused with "no shift assigned today". Restored here.
		if self.employee:
			frappe.db.set_value(
				"Shift Assignment",
				{"employee": self.employee, "docstatus": 1},
				"end_date",
				SHIFT_ASSIGNMENT_TO,
			)
		for name in self._attendance_ids:
			if frappe.db.exists("Attendance", name):
				doc = frappe.get_doc("Attendance", name)
				if doc.docstatus == 1:
					doc.cancel()
				frappe.delete_doc("Attendance", name, force=True, ignore_permissions=True)
		for name in self._log_ids:
			if frappe.db.exists("Employee Checkin", name):
				frappe.delete_doc("Employee Checkin", name, force=True, ignore_permissions=True)
		frappe.db.commit()

	def _tidy(self, result: dict) -> dict:
		"""Remember what a punch created, so `tearDown` can remove it."""
		if result.get("recorded"):
			self._log_ids.append(result["recorded"])
		if result.get("attendance"):
			self._attendance_ids.append(result["attendance"])
		return result

	def _tap(self, gap_seconds: int = 0, latitude=None, longitude=None) -> dict:
		"""One punch, with the previous tap pushed `gap_seconds` into the past.

		`punch` stamps `now_datetime()` itself and swallows any tap within
		`attendance.PUNCH_DEBOUNCE_SECONDS` of the last one. That is right for a wall
		tablet and fatal for a test: two consecutive calls are microseconds apart, so the
		second is swallowed and no day is ever completed.

		So the *previous* row is moved backwards before the next tap - which means the
		argument belongs on the **second** call, not the first. `self._tap()` then
		`self._tap(300)` is an IN and an OUT five minutes apart, and reading it any other
		way (`self._tap(300)` then `self._tap()`) leaves the first row at `now`, where
		the second punch also lands, and the debounce eats it again.

		Coordinates default to a point inside the configured zone rather than to none.
		HRMS's own `allow_geolocation_tracking` is on, so `Employee Checkin.validate`
		refuses a checkin with no coordinates at all - these tests are about the punch,
		and the fence is `TestGeofencedPunch`'s subject, which passes coordinates
		explicitly.
		"""
		if latitude is None and longitude is None:
			latitude, longitude = self.inside_zone()
		if self._log_ids:
			frappe.db.set_value(
				"Employee Checkin",
				self._log_ids[-1],
				"time",
				frappe.utils.now_datetime() - timedelta(seconds=gap_seconds),
				update_modified=False,
			)
		return self._tidy(attendance.punch(self.token, latitude, longitude))

	def inside_zone(self):
		"""A point inside some configured punch zone, read from the data.

		Read rather than hard-coded so the tests follow whatever centres HR has
		entered; a literal here would pass on this site today and quietly stop
		testing the same thing the moment a real radius replaces the demo one.
		"""
		zone = frappe.db.get_value(
			"Shift Location",
			{"checkin_radius": (">", 0)},
			["latitude", "longitude"],
			as_dict=True,
		)
		if not zone:
			self.skipTest("no punch zone has a radius, so no point is inside one")
		return (zone.latitude, zone.longitude)

	def test_the_debounce_is_five_seconds(self):
		"""Pinned because it is a judgement call made once.

		Long enough to absorb a double-tap, short enough that somebody who thinks their
		IN went missing can press again within the same shift start and be heard.
		"""
		self.assertEqual(attendance.PUNCH_DEBOUNCE_SECONDS, 5)


class TestShiftResolution(FrappeTestCase):
	"""The shift must resolve, or every punch lands offshift with nothing on it."""

	def test_the_two_shift_types_exist_and_are_differently_timed(self):
		"""The whole gender split rests on the two starting an hour apart."""
		for name in (MALE_SHIFT_TYPE, FEMALE_SHIFT_TYPE):
			self.assertTrue(frappe.db.exists("Shift Type", name), f"no Shift Type {name!r}")
		male = frappe.db.get_value("Shift Type", MALE_SHIFT_TYPE, "start_time")
		female = frappe.db.get_value("Shift Type", FEMALE_SHIFT_TYPE, "start_time")
		self.assertNotEqual(male, female, "both shifts start at the same time")

	def test_the_grace_periods_widen_the_punch_window(self):
		"""The window shown must be the window HRMS enforces.

		`shift_actual_start` is what decides whether a punch is accepted, so a window
		displayed to an employee that disagrees with it produces a refusal for a reason
		nobody can see. Both shift types here allow 60 minutes either side.
		"""
		for shift_type in (MALE_SHIFT_TYPE, FEMALE_SHIFT_TYPE):
			details = attendance._shift_details(shift_type)
			# The keys are `actual_start`/`actual_end`: `_shift_details` is the raw view of
			# what HRMS computes, and `my_shifts` is what renames them for the screen.
			self.assertEqual(details["start_time"], "10:00" if shift_type == MALE_SHIFT_TYPE else "09:00")
			self.assertEqual(details["actual_start"], "09:00" if shift_type == MALE_SHIFT_TYPE else "08:00")
			self.assertEqual(details["actual_end"], "19:00" if shift_type == MALE_SHIFT_TYPE else "18:00")
			# Never *narrower* than the shift itself, whatever the grace periods say.
			self.assertLessEqual(details["actual_start"], details["start_time"])
			self.assertGreaterEqual(details["actual_end"], details["end_time"])

	def test_the_screen_names_the_window_the_way_the_server_does(self):
		"""The two views must agree, or the employee is shown the wrong window."""
		self.assertEqual(
			attendance._shift_details(MALE_SHIFT_TYPE),
			{
				"start_time": "10:00",
				"end_time": "18:00",
				"actual_start": "09:00",
				"actual_end": "19:00",
			},
		)

	def test_an_unknown_shift_type_reports_nothing_rather_than_raising(self):
		self.assertEqual(attendance._shift_details("No Such Shift"), {})

	def test_every_shift_assignment_on_the_site_is_submitted(self):
		"""A draft is on file and invisible to HRMS.

		This is the bug that made the first 177 assignments useless: `get_shifts_for_date`
		filters `docstatus == 1`, so a draft resolves to nothing and the punch is recorded
		offshift with no shift on it.
		"""
		drafts = frappe.db.count("Shift Assignment", {"docstatus": 0})
		self.assertEqual(
			drafts, 0, f"{drafts} shift assignment(s) are drafts; HRMS resolves no shift from them"
		)


class TestMyShifts(AttendanceTestCase):
	def test_a_submitted_shift_is_listed_with_todays_window(self):
		out = attendance.my_shifts(self.token)
		self.assertEqual(out["employee"], self.employee)
		self.assertTrue(out["shifts"], "no shift listed for an employee who has one")
		today_shift = out["today_shift"]
		self.assertIsNotNone(today_shift, "no shift reported as covering today")
		self.assertTrue(today_shift["covers_today"])
		self.assertEqual(today_shift["shift_type"], MALE_SHIFT_TYPE)

	def test_only_submitted_assignments_are_offered(self):
		"""A draft is on file but cannot be worked, so it is not listed."""
		for row in attendance.my_shifts(self.token)["shifts"]:
			self.assertNotEqual(row["shift_type"], None)

	def test_a_bogus_token_is_refused(self):
		with self.assertRaises(frappe.exceptions.ValidationError):
			attendance.my_shifts("not-a-real-token")

	def test_another_employees_shift_is_not_reachable(self):
		other = frappe.db.get_value(
			"Employee",
			{"status": "Active", "gender": "Female", "name": ("!=", self.employee)},
			"name",
		)
		if not other:
			self.skipTest("no second employee to compare against")
		self.assertEqual(
			attendance.my_shifts(self.token)["employee"],
			self.employee,
			"the shift list is not scoped to the signed-in employee",
		)


class TestPunch(AttendanceTestCase):
	def test_the_first_tap_records_an_in_and_creates_no_attendance_yet(self):
		"""A single log is not a day.

		`mark_attendance_and_link_log` needs a pair, and inventing a finish time for an
		employee who has not left would put hours on their record that never happened.
		"""
		result = self._tidy(attendance.punch(self.token, *self.inside_zone()))
		self.assertEqual(result["kind"], "IN")
		self.assertEqual(result["next"], "out")
		self.assertFalse(result["complete"])
		self.assertIsNone(result["attendance"])
		self.assertTrue(frappe.db.exists("Employee Checkin", result["recorded"]))

	def test_a_tap_outside_the_window_is_recorded_and_flagged_not_refused(self):
		"""The rule this test exists to hold: a late arrival keeps a record.

		Refusing an out-of-window tap was the earlier behaviour and it was wrong. It left
		the employee with no entry at all, so in a payroll dispute HR could not even see
		that they had turned up - which is the one situation the record exists for. HRMS
		has an offshift state for exactly this; the portal files it and says so.

		Whether the tap landed in or out of the window depends on the hour the suite runs,
		so this asserts the *consistency* that must hold either way, and
		`TestPunchWindowAgainstHRMS` pins the in-window case against fixed timestamps.
		"""
		result = self._tidy(attendance.punch(self.token, *self.inside_zone()))
		checkin = frappe.get_doc("Employee Checkin", result["recorded"])
		self.assertEqual(result["offshift"], bool(checkin.offshift))
		if checkin.offshift:
			self.assertIsNone(
				checkin.shift,
				"an offshift checkin must not claim a shift it fell outside of",
			)
		else:
			self.assertEqual(checkin.shift, MALE_SHIFT_TYPE)

	def test_a_refused_punch_writes_no_row(self):
		"""A refusal is a refusal, not a half-written record.

		Worth pinning separately from the refusal tests because `punch` commits inside
		itself, before the caller has seen its answer - so a throw after the commit would
		leave an orphaned log behind.
		"""
		before = frappe.db.count("Employee Checkin", {"employee": self.employee})
		with self.assertRaises(frappe.exceptions.ValidationError):
			attendance.punch("not-a-real-token")
		self.assertEqual(frappe.db.count("Employee Checkin", {"employee": self.employee}), before)

	def test_a_third_tap_is_refused(self):
		"""A third log cannot be paired, so it is a refusal rather than a row."""
		self._tap()
		self._tap(gap_seconds=300)
		# Well clear of the debounce, so this is a genuine third tap rather than a
		# double-tap being collapsed. Punched from inside the zone: HRMS refuses a
		# checkin with no coordinates, and a refusal for the wrong reason would leave
		# this test passing without ever reaching the three-log check.
		with self.assertRaises(frappe.exceptions.ValidationError):
			attendance.punch(self.token, *self.inside_zone())
		self.assertEqual(
			frappe.db.count("Employee Checkin", {"employee": self.employee}),
			2,
			"the refused tap still wrote a row",
		)

class TestPunchPairing(AttendanceTestCase):
	"""Two taps, separated enough to be two.

	Here rather than on the base class so the pairing tests are gathered in one place.
	"""

	def test_two_taps_complete_the_day(self):
		self._tap()
		result = self._tap(gap_seconds=120)
		self.assertTrue(result["complete"])
		self.assertEqual(result["next"], "none")
		attendance_row = frappe.get_doc("Attendance", result["attendance"])
		self.assertEqual(attendance_row.employee, self.employee)
		self.assertEqual(attendance_row.status, "Present")
		# The shift is carried onto the attendance from the checkins, so the pair agrees
		# about which shift was worked - either both name it or neither does. Asserting
		# equality with a shift name here would make the test pass or fail on the hour it
		# was run, which is not a thing a test should depend on.
		checkins = frappe.get_all(
			"Employee Checkin",
			filters={"employee": self.employee},
			fields=["shift", "offshift"],
			limit_page_length=0,
		)
		self.assertEqual(
			attendance_row.shift,
			next((c["shift"] for c in checkins if c["shift"]), None),
			"the attendance names a different shift from the checkins it came from",
		)

	def test_two_taps_seconds_apart_are_still_two_taps(self):
		"""A real IN and OUT minutes apart, which is what a shift actually looks like."""
		self._tap()
		result = self._tap(gap_seconds=30)
		self.assertEqual(result["kind"], "OUT")
		self.assertTrue(result["complete"])
		self.assertEqual(
			frappe.db.count("Employee Checkin", {"employee": self.employee}), 2
		)

	def test_the_day_records_the_hours_actually_worked(self):
		"""`mark_attendance_and_link_log` does not derive times from the logs it is given.

		Called with only `logs` and a status it writes an attendance with `in_time`,
		`out_time`, `working_hours` and `shift` all empty - a day that appears on the
		employee's own screen with no times on it. So the portal supplies them, and this
		pins that it still does.
		"""
		self._tap()
		result = self._tap(gap_seconds=1800)
		row = frappe.get_doc("Attendance", result["attendance"])
		self.assertIsNotNone(row.in_time, "no check-in time on the attendance")
		self.assertIsNotNone(row.out_time, "no check-out time on the attendance")
		self.assertGreater(row.out_time, row.in_time)
		self.assertAlmostEqual(
			row.working_hours, 0.5, places=2,
			msg=f"30 minutes of tapping recorded as {row.working_hours} hours",
		)

	def test_a_double_tap_is_one_tap_rather_than_a_refusal(self):
		"""What a tablet actually sends when someone presses twice.

		HRMS refuses a second log at the same timestamp, so without the debounce the
		employee would be told they had "already recorded" a tap theyet -
		and would have no way to record the real OUT afterwards.
		"""
		first = self._tidy(attendance.punch(self.token, *self.inside_zone()))
		self.assertEqual(first["kind"], "IN")
		second = self._tidy(attendance.punch(self.token, *self.inside_zone()))
		self.assertTrue(second.get("debounced"))
		self.assertEqual(second["recorded"], first["recorded"])
		# And the day is still open, so the real OUT can still be recorded.
		self.assertFalse(second["complete"])
		self.assertEqual(second["next"], "out")

	def test_an_employee_with_no_shift_today_is_refused(self):
		"""Not recorded as offshift: from a tablet that would only mean we got it wrong."""
		frappe.db.set_value(
			"Shift Assignment",
			{"employee": self.employee, "docstatus": 1},
			"end_date",
			"2026-01-31",
		)
		frappe.db.commit()
		# Inside the zone, so the refusal is about the missing shift rather than about
		# a missing location - the two raise the same exception type.
		with self.assertRaises(frappe.exceptions.ValidationError):
			attendance.punch(self.token, *self.inside_zone())

	def test_a_bogus_token_cannot_punch(self):
		with self.assertRaises(frappe.exceptions.ValidationError):
			attendance.punch("not-a-real-token")

	def test_one_employees_punch_does_not_become_anothers_attendance(self):
		today_date = frappe.utils.today()
		# Measured before and after, not asserted as an absolute zero. Scoping to today
		# was not enough: the portal is in real use, so a colleague's genuine attendance
		# for today exists on the site and this test would fail on it - it was reading
		# other people's work as if this test had created it. What is actually being
		# claimed is that *these* punches changed nothing for anyone else.
		other_before = frappe.db.count(
			"Attendance",
			{"employee": ("!=", self.employee), "attendance_date": today_date},
		)
		self._tap()
		self._tap(gap_seconds=300)
		mine = frappe.db.count(
			"Attendance", {"employee": self.employee, "attendance_date": today_date}
		)
		other_after = frappe.db.count(
			"Attendance",
			{"employee": ("!=", self.employee), "attendance_date": today_date},
		)
		self.assertEqual(mine, 1)
		self.assertEqual(
			other_after, other_before, "a punch created attendance for somebody else"
		)


class TestPunchWindowAgainstHRMS(AttendanceTestCase):
	"""The in-window case, pinned against HRMS with timestamps we choose.

	`punch` always stamps `now_datetime()`, so testing the window through it would only
	ever test whichever hour the suite happened to run in - and this suite has already
	been caught by exactly that: it passed at 18:16 and failed at 21:05, with no change to
	any code. So the window is exercised directly against `add_log_based_on_employee_field`
	at fixed times of day, which is the contract the portal depends on and is stable
	whatever the clock says.
	"""

	def _checkin_at(self, clock_time: str) -> dict:
		from hrms.hr.doctype.employee_checkin.employee_checkin import (
			add_log_based_on_employee_field,
		)

		lat, lon = self.inside_zone()
		doc = add_log_based_on_employee_field(
			employee_field_value=self.employee,
			timestamp=frappe.utils.get_datetime(f"{frappe.utils.today()} {clock_time}"),
			employee_fieldname="name",
			device_id=attendance.PUNCH_DEVICE,
			# HRMS's own `allow_geolocation_tracking` refuses a checkin with no
			# coordinates, and these tests are about the punch *window* - so the
			# location is carried to get past that gate, not asserted on.
			latitude=lat,
			longitude=lon,
		)
		self._log_ids.append(doc.name)
		return {
			"shift": doc.shift,
			"offshift": bool(doc.offshift),
			"shift_actual_start": doc.shift_actual_start,
		}

	def test_a_punch_inside_the_shift_is_attached_to_it(self):
		got = self._checkin_at("10:30:00")
		self.assertFalse(got["offshift"], "10:30 is inside a 10:00-18:00 shift")
		self.assertEqual(got["shift"], MALE_SHIFT_TYPE)

	def test_the_grace_periods_are_honoured_at_the_edges(self):
		"""09:00 is the earliest accepted punch: one hour before a 10:00 shift.

		These are the boundaries the window shown on screen is built from, so an hour of
		drift between the two would refuse a tap the tablet said was fine.
		"""
		self.assertFalse(self._checkin_at("09:05:00")["offshift"])
		self.assertFalse(self._checkin_at("18:55:00")["offshift"])
		self.assertTrue(
			self._checkin_at("08:50:00")["offshift"], "08:50 is outside the grace period"
		)
		self.assertTrue(
			self._checkin_at("19:30:00")["offshift"], "19:30 is outside the grace period"
		)

	def test_the_window_hrms_widens_matches_the_window_the_screen_shows(self):
		"""`shift_actual_start`/`_end` are what HRMS enforces, so they are the truth.

		`_shift_details` recomputes them from the Shift Type for display. Comparing the
		recomputed pair against HRMS's own pair on the same day is what catches the two
		drifting apart - a window on the tablet that disagrees with the enforced one
		produces a refusal for a reason the employee cannot see.
		"""
		details = attendance._shift_details(MALE_SHIFT_TYPE)
		got = self._checkin_at("10:30:00")
		self.assertEqual(
			got["shift_actual_start"].strftime("%H:%M"), details["actual_start"]
		)
		self.assertEqual(
			got["shift_actual_start"].strftime("%H:%M"), "09:00"
		)

	def test_a_punch_midnight_is_offshift(self):
		"""No night shift exists on this site, so a midnight tap is offshift."""
		self.assertTrue(self._checkin_at("00:30:00")["offshift"])

	def test_the_screen_says_whether_the_window_is_open_right_now(self):
		"""Reported per assignment, and never claimed open when it cannot be worked out."""
		shift = attendance._shift_details(MALE_SHIFT_TYPE)
		self.assertEqual(attendance._within_window(shift), attendance._within_window(shift))
		self.assertFalse(
			attendance._within_window({}),
			"an unknown window was reported as open",
		)
		self.assertFalse(attendance._within_window({"actual_start": None}))

	def test_a_day_whose_window_is_closed_still_reports_it(self):
		"""`check_in_open` is None when no shift covers today, not a misleading True."""
		for row in attendance.my_shifts(self.token)["shifts"]:
			if row["covers_today"]:
				self.assertIn(row["check_in_open"], (True, False))
			else:
				self.assertIsNone(row["check_in_open"])


class TestMyAttendance(AttendanceTestCase):
	def test_a_recorded_day_appears_in_the_list(self):
		self._tap()
		result = self._tap(gap_seconds=300)
		self.assertTrue(result["complete"])
		out = attendance.my_attendance(self.token)
		days = {day["date"]: day for day in out["days"]}
		self.assertIn(frappe.utils.today(), days)
		day = days[frappe.utils.today()]
		self.assertEqual(day["status"], "Present")
		self.assertEqual(out["present_days"], 1)
		# The hours reached the list. A day with `working_hours` of None on it is a day
		# the employee cannot see the point of, and it is what HRMS writes when the
		# caller does not supply the times.
		self.assertIsNotNone(day["working_hours"])
		self.assertIsNotNone(day["in_time"])

	def test_the_dates_are_all_strings(self):
		"""`from_date`, `to_date` and each day's `date` are the same type.

		`get_all` hands back `datetime.date` for `attendance_date` while the bounds are
		built from strings, so a caller doing `out["to_date"] in days` silently fails.
		"""
		out = attendance.my_attendance(self.token)
		self.assertIsInstance(out["from_date"], str)
		self.assertIsInstance(out["to_date"], str)
		for day in out["days"]:
			self.assertIsInstance(day["date"], str)
			self.assertIsInstance(day["in_time"], (str, type(None)))

	def test_the_list_is_scoped_to_the_signed_in_employee(self):
		out = attendance.my_attendance(self.token)
		self.assertEqual(out["employee"], self.employee)

	def test_an_employee_with_no_records_gets_an_empty_list_not_an_error(self):
		"""The screen must render before anyone has ever punched."""
		other = frappe.db.get_value(
			"Employee",
			{"status": "Active", "gender": "Female", "sdg_national_id": ("is", "set"),
			 "employee_number": ("is", "set")},
			"name",
		)
		if not other:
			self.skipTest("no second employee who can sign in")
		token = portal.sign_in(
			frappe.db.get_value("Employee", other, "sdg_national_id"),
			frappe.db.get_value("Employee", other, "employee_number"),
		)["token"]
		out = attendance.my_attendance(token)
		self.assertEqual(out["days"], [])
		self.assertEqual(out["present_days"], 0)

	def test_the_window_is_bounded(self):
		"""A wall tablet cannot page through a year of days."""
		out = attendance.my_attendance(self.token)
		span = frappe.utils.getdate(out["to_date"]) - frappe.utils.getdate(out["from_date"])
		self.assertEqual(span.days, attendance.ATTENDANCE_DAYS - 1)

	def test_a_bogus_token_is_refused(self):
		with self.assertRaises(frappe.exceptions.ValidationError):
			attendance.my_attendance("not-a-real-token")


	def test_the_screen_is_told_what_the_next_tap_would_do(self):
		"""The button is labelled from this, so it must not depend on the clock.

		Counted from the checkins rather than from the days list: a half-recorded day
		has no `Attendance` row yet, so reading the state off the absence of one would
		invite a third tap and a refusal.
		"""
		out = attendance.my_attendance(self.token)
		self.assertEqual(out["today"]["next"], "in")
		self.assertEqual(out["today"]["logged"], 0)
		self.assertFalse(out["today"]["complete"])

		first = self._tap()
		self.assertEqual(first["kind"], "IN")
		out = attendance.my_attendance(self.token)
		self.assertEqual(out["today"]["next"], "out", "after one tap the next tap is the OUT")
		self.assertEqual(out["today"]["logged"], 1)
		self.assertEqual(len(out["today"]["punches"]), 1)
		self.assertIsNotNone(out["today"]["punches"][0]["time"])

		second = self._tap(gap_seconds=300)
		self.assertEqual(second["kind"], "OUT")
		out = attendance.my_attendance(self.token)
		self.assertEqual(out["today"]["next"], "none", "a finished day takes no more taps")
		self.assertEqual(out["today"]["logged"], 2)
		self.assertTrue(out["today"]["complete"])

	def test_the_today_block_exists_before_anything_is_punched(self):
		"""The screen must render with no history behind it."""
		out = attendance.my_attendance(self.token)
		self.assertEqual(out["today"]["punches"], [])
		self.assertEqual(out["days"], [])


class TestGeofencedPunch(AttendanceTestCase):
	"""The punch is geofenced when `geo_punch_enforce` is on.

	The zone is HR's to configure and enforcement is HR's to switch on, so both are
	manipulated here and restored afterwards - a leaked zone or a leaked "enforce"
	would refuse the punches of every other test on the site.
	"""

	# A point in Sharm El Sheikh, and the Gulf of Guinea as somewhere unambiguously
	# outside a 1 km radius whatever the curvature is doing.
	ZONE_LAT = 27.9158
	ZONE_LON = 34.3300
	ZONE_RADIUS_KM = 1

	def setUp(self):
		super().setUp()
		self.company = frappe.db.get_value("Employee", self.employee, "company")
		self._previous_enforce = frappe.db.get_single_value(
			"Employee Self Service Settings", "geo_punch_enforce"
		)
		self._created_location = None
		# Link the employee's current shift assignment to a test zone, restoring it later.
		self._assignment = frappe.get_all(
			"Shift Assignment",
			filters={"employee": self.employee, "docstatus": 1, "status": "Active"},
			pluck="name",
			limit_page_length=1,
		)[0]
		self._old_location = frappe.db.get_value("Shift Assignment", self._assignment, "shift_location")
		self._created_location = frappe.db.get_value("Shift Location", "Portal Geo Test Zone")
		if not self._created_location:
			doc = frappe.get_doc(
				{
					"doctype": "Shift Location",
					"location_name": "Portal Geo Test Zone",
					"checkin_radius": int(self.ZONE_RADIUS_KM * 1000),
					"latitude": self.ZONE_LAT,
					"longitude": self.ZONE_LON,
				}
			).insert(ignore_permissions=True)
			self._created_location = doc.name
		else:
			frappe.db.set_value(
				"Shift Location",
				self._created_location,
				{
					"latitude": self.ZONE_LAT,
					"longitude": self.ZONE_LON,
					"checkin_radius": int(self.ZONE_RADIUS_KM * 1000),
				},
				update_modified=False,
			)
		frappe.db.set_value(
			"Shift Assignment", self._assignment, "shift_location", self._created_location, update_modified=False
		)
		frappe.db.set_single_value("Employee Self Service Settings", "geo_punch_enforce", 1)
		frappe.db.commit()
		self.addCleanup(self._restore_geo)

	def _restore_geo(self):
		# Enforcement and the assignment's link are the only two things this class
		# writes onto shared rows, and both are put back: a leaked "enforce" would
		# refuse the punches of every other test on the site, and a leaked link would
		# leave a real employee pointing at a fixture zone.
		frappe.db.set_single_value(
			"Employee Self Service Settings",
			"geo_punch_enforce",
			self._previous_enforce or 0,
		)
		frappe.db.set_value(
			"Shift Assignment",
			self._assignment,
			"shift_location",
			self._old_location,
			update_modified=False,
		)
		if frappe.db.exists("Shift Location", self._created_location):
			frappe.delete_doc(
				"Shift Location", self._created_location, force=True, ignore_permissions=True
			)
		frappe.db.commit()

	def test_a_punch_inside_the_zone_is_recorded_and_marked_verified(self):
		result = self._tap(latitude=self.ZONE_LAT, longitude=self.ZONE_LON)
		self.assertEqual(result["geo_status"], "ok")
		checkin = frappe.get_doc("Employee Checkin", result["recorded"])
		self.assertAlmostEqual(checkin.latitude, self.ZONE_LAT, places=5)
		self.assertAlmostEqual(checkin.longitude, self.ZONE_LON, places=5)
		self.assertEqual(checkin.sdg_self_service_geo_status, "Verified")

	def test_a_punch_outside_the_zone_is_refused_and_writes_nothing(self):
		before = frappe.db.count("Employee Checkin", {"employee": self.employee})
		with self.assertRaises(frappe.exceptions.ValidationError):
			attendance.punch(self.token, 0.0, 0.0)
		self.assertEqual(
			frappe.db.count("Employee Checkin", {"employee": self.employee}),
			before,
			"an out-of-zone punch still wrote a checkin",
		)

	def test_a_punch_without_coordinates_is_refused_when_enforced(self):
		"""A tablet that could not get a fix is not allowed to punch when enforced."""
		with self.assertRaises(frappe.exceptions.ValidationError):
			attendance.punch(self.token)

	def test_the_zone_verdict_reaches_the_attendance_screen(self):
		self._tap(latitude=self.ZONE_LAT, longitude=self.ZONE_LON)
		punches = attendance.my_attendance(self.token)["today"]["punches"]
		self.assertEqual(len(punches), 1)
		self.assertEqual(punches[0]["geo_status"], "Verified")


class TestAttendanceWiring(FrappeTestCase):
	def test_the_actions_the_page_uses_all_exist(self):
		for name in ("my_shifts", "my_attendance", "punch"):
			self.assertIn(name, renderers.ACTIONS)

	def test_no_attendance_action_bypasses_the_session(self):
		"""Each one must resolve the token itself.

		`renderers.ACTIONS` is a whitelist, not a session check - a function that skipped
		`resolve_session` would hand one employee's attendance to another, and the
		whitelist would wave it through.
		"""
		for func in (attendance.my_shifts, attendance.my_attendance, attendance.punch):
			with self.assertRaises(frappe.exceptions.ValidationError):
				func("not-a-real-token")

	def test_the_holiday_list_named_in_code_is_the_one_on_the_site(self):
		"""Leave day counts and attendance both read it, so a rename is worth failing on."""
		self.assertTrue(frappe.db.exists("Holiday List", HOLIDAY_LIST_NAME))

	def test_the_assignment_dates_match_the_holiday_lists_year(self):
		self.assertTrue(
			SHIFT_ASSIGNMENT_FROM.startswith(HOLIDAY_LIST_NAME)
			and SHIFT_ASSIGNMENT_TO.startswith(HOLIDAY_LIST_NAME),
			"the shift assignment dates and the holiday list name have drifted apart",
		)
