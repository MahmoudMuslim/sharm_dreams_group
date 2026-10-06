// Copyright (c) 2026, Sharm Dreams Group and contributors
// For license information, see license.txt

frappe.provide("frappe.dashboards.chart_sources");

// A `Custom` chart finds its data through this declaration, so the file is the whole
// contract between the browser and `monthly_joiners.get_data`. `setup` writes the
// matching `Dashboard Chart Source` record from the name below, so the two have to
// agree exactly - there is no second place that says so.
//
// No company filter, and no default: the method widens a chosen company to its
// subtree and treats "no company" as every company, so an unset filter covers the
// whole group and a defaulted one would quietly narrow it. See
// sharm_dreams_group/upstream_patches.py for the same widening on the stock sources.
frappe.dashboards.chart_sources["Monthly Joiners"] = {
	method: "sharm_dreams_group.hr.dashboard_chart_source.monthly_joiners.monthly_joiners.get_data",
};
