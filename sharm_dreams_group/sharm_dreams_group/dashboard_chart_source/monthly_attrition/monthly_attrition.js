// Copyright (c) 2026, Sharm Dreams Group and contributors
// For license information, see license.txt

frappe.provide("frappe.dashboards.chart_sources");

// The other half of the split; see `monthly_joiners.js` for why a chosen company
// widens to its subtree and an unset one means every company, rather than carrying a
// default that would quietly narrow the answer.
frappe.dashboards.chart_sources["Monthly Attrition"] = {
	method: "sharm_dreams_group.hr.dashboard_chart_source.monthly_attrition.monthly_attrition.get_data",
};
