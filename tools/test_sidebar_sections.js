// Exercise the shipped side-panel JS with a stubbed Frappe.
//
// `sidebar_sections.js` is a deliberate no-op placeholder: its global sidebar
// patches were removed at the owner's request. What can still fail silently is
// the removal itself - a pasted-back prototype patch would restyle every sidebar
// in the desk again, and nothing else in the suite runs the file. So this loads
// it against a `frappe.ui.Sidebar` whose assignment sites are instrumented, and
// checks the placeholder changes nothing.
//
// Run: node tools/test_sidebar_sections.js
// Prints one JSON object on stdout; a non-zero exit means a check failed.

const fs = require("fs");
const path = require("path");
const vm = require("vm");

const APP = path.resolve(__dirname, "..");
const SRC = fs.readFileSync(
	path.join(APP, "sharm_dreams_group", "public", "js", "sidebar_sections.js"),
	"utf8"
);

const results = [];
function check(name, ok, detail) {
	results.push({ name, ok: !!ok, detail: detail === undefined ? null : detail });
}

// Track every property the file assigns onto the stubbed surfaces. An empty set
// after the run is the whole point of the placeholder.
const written = [];
function tracker(base, label) {
	return new Proxy(base, {
		set(target, prop, value) {
			written.push(`${label}.${String(prop)}`);
			target[prop] = value;
			return true;
		},
	});
}

const sidebarProto = {};
const sidebarClass = { prototype: tracker(sidebarProto, "frappe.ui.Sidebar.prototype") };
const frappe = {
	ui: {
		// the `prototype` slot is filled before the class object is wrapped, so
		// this setup does not show up as a write by the file under test
		Sidebar: tracker(sidebarClass, "frappe.ui.Sidebar"),
	},
};

// The desk's other globals, present so an accidental use is recorded rather than
// throwing, which would pass as a "load error" and hide what was done.
frappe.provide = (name) => {
	written.push(`frappe.provide(${name})`);
};
const $ = () => {
	written.push("jQuery call");
};

const context = vm.createContext({
	frappe,
	$: tracker($, "$"),
	jQuery: $,
	console,
	// no `document`: a patch that reaches for the DOM would throw, and a throw
	// here is a failure rather than silence.
});

let loadError = null;
try {
	vm.runInContext(SRC, context, { filename: "sidebar_sections.js" });
} catch (e) {
	loadError = String(e && e.message ? e.message : e);
}

check("the file loads against the desk stubs without throwing", loadError === null, loadError);
check("no prototype methods are written", written.filter((w) => w.startsWith("frappe.ui.Sidebar")).length === 0, written);
check("no globals are provided", written.filter((w) => w.startsWith("frappe.provide")).length === 0, written);

// and it still ships: the bundle has to import it, because the placeholder's
// whole contract is "the import list does not churn".
const bundle = fs.readFileSync(
	path.join(APP, "sharm_dreams_group", "public", "js", "sharm_dreams_group.bundle.js"),
	"utf8"
);
check("the bundle still imports it", bundle.includes('import "./sidebar_sections.js"'), null);

const payload = JSON.stringify(
	{ total: results.length, failed: results.filter((r) => !r.ok).length, results },
	null,
	1
);
process.stdout.write(payload);
if (process.env.SHARM_REPORT) {
	try {
		fs.writeFileSync(process.env.SHARM_REPORT, payload);
	} catch (e) {
		// not being able to write the file is not a reason to fail the run
	}
}
process.exitCode = results.every((r) => r.ok) ? 0 : 1;
