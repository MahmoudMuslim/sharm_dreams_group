// Copyright (c) 2026, Sharm Dreams Group and contributors
// For license information, see license.txt

// Global sidebar patches REMOVED per user request.
// This file is kept as a no-op placeholder to avoid breaking the bundle import.

// The following patches were removed because they affected ALL sidebars globally:
// - frappe.ui.Sidebar.prototype.create_sidebar (nested sections, section icons, internal URLs)
// - frappe.ui.Sidebar.prototype.load_sidebar_data (re-nesting)
// - frappe.ui.Sidebar.prototype.sharm_close_kept_closed_sections
// - frappe.ui.Sidebar.prototype.draw_section_icons
// - frappe.ui.Sidebar.prototype.open_internal_urls_in_this_tab
//
// If you need these features for a specific sidebar, apply them via Workspace
// configuration or a targeted patch that checks the current module/sidebar.