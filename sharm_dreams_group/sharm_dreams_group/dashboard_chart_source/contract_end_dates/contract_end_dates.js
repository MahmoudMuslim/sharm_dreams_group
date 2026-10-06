frappe.provide("frappe.dashboards.chart_sources");

frappe.dashboards.chart_sources["Contract End Dates"] = {
	// The path is long because the desk resolves a chart source's file through
	// `get_module_path(module)` - which always appends the scrubbed module name -
	// so the source has to live in the module folder, beside the app's doctypes.
	// Moving it up to the package root would leave it found by nothing.
	method: "sharm_dreams_group.sharm_dreams_group.dashboard_chart_source.contract_end_dates.contract_end_dates.get_data",
	filters: [
		{
			// No `default` and no `reqd`. The method widens a chosen company to its
			// subtree and reads a blank one as the whole group, so a default here
			// would silently re-scope the chart to a single company and undo that.
			// The upstream sources this desk also ships *do* declare such a default,
			// which is what sharm_dreams_group/upstream_patches.py exists to remove.
			fieldname: "company",
			label: __("Company"),
			fieldtype: "Link",
			options: "Company",
		},
	],
};