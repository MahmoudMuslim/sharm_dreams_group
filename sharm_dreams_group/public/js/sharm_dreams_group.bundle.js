// Copyright (c) 2026, Sharm Dreams Group and contributors
// For license information, see license.txt

// The app's desk JavaScript, as one content-hashed bundle.
//
// This file exists for one reason: nginx serves everything under `/assets/` with
//
//     Cache-Control: max-age=31536000
//
// unconditionally, for a year, with no revalidation.  A file named
// `/assets/sharm_dreams_group/js/dashboard_view.js` therefore never changes URL,
// so once a browser has it, that copy is used until the user clears the cache by
// hand.  That is how a fix can be on disk, served correctly, and still not reach
// the browser - the desk went on loading code whose line numbers no longer existed
// in the file it was supposedly running.
//
// Anything Frappe resolves through `bundled_asset` is looked up in `assets.json`,
// which maps a `*.bundle.js` name to a hashed output path.  A changed file changes
// the hash, so the URL changes, so the browser refetches.  That is the whole fix;
// the import list below is otherwise the same five files, in the same order.
//
// Order matters.  Each of these runs side effects at load time - patching
// prototypes, binding `sidebar_setup`, reading `frappe.boot` - and the patches
// they install are read by the next one.  esbuild runs imported modules in the
// order they are imported here, which is the order `app_include_js` used to give
// them.
//
// Adding a file here also means adding it to `hooks.py`: `app_include_js` names
// this one bundle, so a file left out of this list is simply not shipped.  A test
// checks the two lists agree.

import "./dashboard_view.js";
import "./sidebar_sections.js";
import "./dashboard_widgets.js";
import "./company_tree.js";
import "./employee_penalty.js";