import frappe
from frappe.utils import getdate, get_datetime, time_diff_in_hours

def run():
	emp = "V0130"
	on_date = "2026-10-08"
	today_date = getdate(on_date)

	# 1. Check holiday list
	from sharm_dreams_group.portal.portal import holiday_list_for
	holiday_list = compensatory._employee_holiday_list(emp, today_date)
	print("Holiday list:", holiday_list)

	if holiday_list:
		is_holiday = compensatory._is_holiday(today_date, holiday_list)
		print("Is holiday:", is_holiday)

		weekly_off_days = compensatory._weekly_off_days(holiday_list)
		print("Weekly off days:", weekly_off_days)
		is_weekly_off = getdate(today_date).weekday() in weekly_off_days
		print("Is weekly off:", is_weekly_off)

		# 2. Check shift
		shift = compensatory._employee_workday_shift(emp, today_date)
		print("Shift:", shift)

		# 3. Check checkins
		checkins = compensatory._checkins_on(today_date, emp)
		print("Checkins:", len(checkins))
		for c in checkins:
			print(f"  {c.log_type} at {c.time}")

		# 4. Compute hours
		if shift and checkins:
			hours = compensatory._hours_worked(checkins, shift["start_time"], shift["end_time"])
			print("Hours worked:", hours)

			if hours > 0:
				earnable_type = None
				from sharm_dreams_group.portal import leave_policy
				multipliers = leave_policy.get_earnable_type_multipliers()
				print("Multipliers:", multipliers)

				if compensatory._is_holiday(today_date, holiday_list):
					earnable_type = next(
						(n for n, m in multipliers.items() if m == 2.0 and "Public Holiday" in n),
						None,
					)
				print("Earnable type:", earnable_type)

				if earnable_type:
					multiplier = multipliers.get(earnable_type, 2.0)
					from sharm_dreams_group.portal.leave_policy import _compute_credit
					credit = _compute_credit(hours_worked=hours, multiplier=multipliers[earnable_type])
					print(f"Hours: {hours}, multiplier: {multipliers[earnable_type]}, credit: {credit}")

	frappe.db.rollback()