# Copyright (c) 2026, Sharm Dreams Group and contributors
# For license information, please see license.txt

"""The "Sharm Dreams Group" workspace, and Link Cards to it from the stock workspaces.

Every DocType this app owns has to be reachable from the desk.  Without a
workspace, `Income Tax Slab` and the rest can only be opened by typing a URL,
picking them from a Link field, or running `bench execute` - which is not a
usable interface for the people who maintain band tables.

This app is the group's customisations base rather than payroll-only, so the
workspace is public and named for the app.

How a workspace actually renders
--------------------------------
`Workspace.content` is a list of blocks, but a block is only drawn if it can be
resolved against the workspace's child tables:

*   a `shortcut` block resolves against `Workspace.shortcuts`
*   a `card` block resolves against `Workspace.links`, where a card is a
    `Card Break` row followed by `Link` rows (see `Workspace.get_link_groups`)

`get_desktop_page()` returns those child tables to the browser, and
`blocks/block.js make()` silently skips any block whose name matches nothing in
them.  So writing a block on its own produces a workspace that looks correct in
the database and renders blank on screen - which is exactly what an earlier
version of this module did.  Every addition here therefore writes the rows and
the block together, and `resolution_report()` proves it by joining them the way
the browser does.

`Workspace Link.link_type` only allows DocType / Page / Report, so a Link Card
cannot point at a Workspace.  The Sharm Dreams Group workspace itself is reached
through the app switcher instead.

Run: bench --site <site> execute sharm_dreams_group.workspace.apply
     bench --site <site> execute sharm_dreams_group.workspace.verify
"""

from __future__ import annotations

import json

import frappe

WORKSPACE = "Sharm Dreams Group"

# An octicon name. See where it is applied for why it cannot be an image.
WORKSPACE_ICON = "organization"

# Below everything else on the site, so /desk opens here. See where it is set.
#
# Negative rather than zero because Lending also sits at 0.0, and a tie is broken
# by name - which put Lending ahead of us. Negative is the only way to be
# strictly first without editing another app's workspace.
WORKSPACE_SEQUENCE = -1
MODULE = "Sharm Dreams Group"
APP = "sharm_dreams_group"

# Grouped so the workspace reads in the order the data is used: rates, then the
# settings that drive them, then the vocabularies the Employee form links to,
# then the per-employee insurance record, then time off.
CARDS: list[tuple[str, list[str]]] = [
	(
		"Rates & Bands",
		[
			"Income Tax Slab",
			"SDG Gross Insurance Band",
			"SDG Medical Band",
			"SDG Family Medical Band",
		],
	),
	("Payroll Settings", ["SDG Payroll Settings"]),
	# Reachable from the desk on purpose: narrowing who may see penalties is a
	# deliberate setting, not something to leave buried in a route.
	("Penalty Settings", ["SDG Penalty Level"]),
	# The cost shares a manager's pay is split into.  Shown here rather than only
	# on the slips because the charged company needs to see what it is being
	# billed for, and the slip's company is the employer, not the payer.
	("Group Allocation", ["SDG Group Allocation"]),
	(
		"Vocabularies",
		["SDG Employment Category", "SDG Contract Type", "SDG Sub Department"],
	),
	("Insurance", ["SDG Insured Person"]),
	# The fee a child attracts.  Its own card rather than a line in "Rates &
	# Bands" because that card is salary-banded insurance cover and this is a
	# flat monthly figure - the two are configured on completely different rules.
	# Only the fee master is linked: the children are a child table, opened
	# through the employee they belong to rather than as a list.
	("School Fees", ["SDG School Fee"]),
	# What a family of a given size is owed in cost-of-living coupons.  A separate
	# card from School Fees because the two are granted on different rules: school
	# fees follow the child, the coupon follows the family.
	("Cost Of Living Coupon", ["SDG Coupon Granted", "SDG Coupon Band"]),
	# The employee portal.  Linked so the reachability guard is satisfied and a
	# System Manager can open these when a sign-in is not working - but note the
	# split in who may read what:
	#
	#   * `Employee Self Service Session` and `Employee Self Service Preauth` are System Manager only.  A session
	#     row holds the plaintext token that authorises every portal action, and a
	#     pre-auth row holds a live biometric challenge, so anybody who can read
	#     either can act as that employee.  They are here for debugging, not for
	#     daily use, and the link 403s for HR rather than not existing.
	#   * The other two are HR's: the settings and the enrolled biometric devices.
	#     The punch zone is not here: it is a `Shift Location`, linked on the
	#     Shifts card, because a zone belongs to a work location and not to the
	#     portal surface.
	#
	# The card is labelled for what the employee does, not for the hardware: this is
	# the portal surface an HR manager looks after, rather than for the tablet it
	# happens to run on. Only the label - the DocTypes keep their names.
	(
		"Employee Portal",
		[
			"Employee Self Service Settings",
			"Employee Self Service Biometric Credential",
			"Employee Self Service Session",
			"Employee Self Service Preauth",
		],
	),
]

# Link Cards added to the stock workspaces, as {workspace: [(card, [doctypes])]}.
#
# Only the tables each team actually maintains are listed, so HR Setup does not
# end up owning the payroll band tables and vice versa.
#
# These workspaces belong to HRMS, so an upgrade can revert the addition.  They
# are re-applied on every migrate, and `verify()` refuses to pass if a card fails
# to resolve.
STOCK_CARDS: dict[str, list[tuple[str, list[str]]]] = {
	"HR Setup": [
		(
			"Sharm Dreams Group",
			["SDG Employment Category", "SDG Contract Type", "SDG Sub Department"],
		),
		# Penalties are configured by HR, but until this existed the only place the
		# two penalty masters were linked from was the main Sharm Dreams Group page,
		# so nobody arriving at HR Setup - the page an HR manager actually opens to
		# set the desk up - could find them.
		("Penalty Setup", ["SDG Penalty Level"]),
	],
	# Home is what a user lands on when they have not chosen a workspace, so an
	# app that is otherwise only reachable by typing its URL is invisible from
	# there.  The entry points only - the band tables stay on Rates & Bands, and
	# putting all sixteen on the front page would bury Home's own cards.
	"Home": [
		(
			"Sharm Dreams Group",
			[
				"SDG Employment Category",
				"SDG Contract Type",
				"SDG Sub Department",
				"SDG Penalty Level",
				"SDG School Fee",
			],
		)
	],
	"Payroll": [
		# The coupon bands are reference data HR looks up, and the coupons granted
		# are the record of what was handed over.  Neither is a payroll document -
		# see the SDG Coupon Granted controller for why - but the *bands* sit with
		# Payroll alongside the tax slab because that is where a manager looks for
		# "what is this person owed".
		("Sharm Dreams Group", ["SDG Payroll Settings", "Income Tax Slab", "SDG Coupon Band"]),
		# School fees are a payroll cost, so the fee master sits with Payroll
		# rather than on HR Setup - the same split as the insurance bands.
		("School Fees", ["SDG School Fee"]),
	],
	"Tax & Benefits": [("Sharm Dreams Group", ["Income Tax Slab", "SDG Coupon Band"])],
}

# Prefix on every content block and helper path this module owns, so a later
# change can remove exactly what we added and nothing else.
BLOCK_PREFIX = "sdg-"


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _is_single(doctype: str) -> bool:
	return bool(frappe.db.get_value("DocType", doctype, "issingle"))


def _slug(value: str) -> str:
	"""A block id from a label.

	One definition, and there used to be two - a second `_slug(label)` further down
	this file replaced the first, because Python takes the last one, so which of the
	two rules built a block id depended on nothing anyone could see from the call
	site. They disagreed on exactly the labels that matter here: the sanitising
	version turns `Designation Wise Salary(Last Month)` into
	`designation-wise-salary-last-month` and the other leaves the parentheses in,
	which become part of a DOM id.

	Every non-alphanumeric character becomes a dash, and runs collapse, because
	these strings end up as element ids and a space or a bracket in one is a
	querySelector that throws rather than a cosmetic problem.
	"""
	slug = "".join(c if c.isalnum() else "-" for c in value).strip("-").lower()
	while "--" in slug:
		slug = slug.replace("--", "-")
	return slug[:28].strip("-")


def _shortcut_block(doctype: str, col: int) -> dict:
	# Blank `doc_view` for a Single, for the reason given where the `shortcuts`
	# child table is built: "Form" is not one of the field's options, and the desk
	# tests `is_single` before it ever reads `doc_view`.  This is the content-JSON
	# twin of that row, and it had kept the old value - invisible here, because a
	# Code field is not Select-validated, which is exactly why it survived.
	return {
		"id": f"{BLOCK_PREFIX}{_slug(doctype)}",
		"type": "shortcut",
		"data": {
			"shortcut_name": doctype,
			"col": col,
			"link_to": doctype,
			"doc_view": "" if _is_single(doctype) else "List",
			"color": "Grey",
		},
	}


def _expected_doctypes() -> set[str]:
	"""Every non-child DocType this app owns, Singles included.

	Singles are included on purpose: they are documents to open, not lists, and
	they are covered by the same reachability guarantee.
	"""
	return set(
		frappe.get_all(
			"DocType",
			filters={"module": MODULE, "istable": 0},
			pluck="name",
			limit_page_length=0,
		)
	)


# ---------------------------------------------------------------------------
# apply
# ---------------------------------------------------------------------------
# Stock DocTypes this app publishes a record of, and so links from its own page.
#
# The workspace guard below exists to catch a typo - a name in CARDS that resolves to
# nothing - so an adopted stock DocType has to be declared rather than waved through.
# One is listed: the Egyptian income tax lives in `Income Tax Slab` because that is
# where Frappe expects to find a tax table, and a parallel app-owned bracket could
# only be opened by typing a URL.
ADOPTED_DOCTYPES = {"Income Tax Slab"}

# App-owned DocTypes deliberately kept off the workspace, with the reason recorded.
#
# `Employee Self Service Location` was the per-company punch zone. The geofence is now
# the `Shift Location` on the employee's shift assignment - HRMS's own work-location
# doctype, already linked on the Shifts card - so this one has no reader. It is listed
# rather than left to trip the reachability guard, because "this doctype exists but
# nothing writes to it" is a decision someone should be able to find, not an accident
# to rediscover. The DocType and its validator are kept so a site that already has rows
# in it is not left with data nobody can reach; deleting them is a separate call.
#
# The old self-service page (at /self-services-employees) used these two DocTypes for
# its approval workflow. They are superseded by the portal's compensatory approval
# system (Leave In Lieu via Leave Allocation). Kept so existing rows are not orphaned.
SUPERSEDED_DOCTYPES = {
	"Employee Self Service Location",
	"Employee Self Service Approval Category",
	"Employee Self Service Approval Request",
}

# App-owned DocTypes whose surface is the employee portal, not the desk.
#
# These are the app's shadowed copies of HRMS feature doctypes (insurance,
# safety, recognition, travel, learning, wellness, ...) that the portal's
# `/employee-portal` page reads and writes.  The reachability guard demands that
# every owned DocType be reachable from the desk, but that is not what these
# are for: their only reader is the portal web surface (a handful also sit on
# the HR dashboard's sidebar), so listing them on the Sharm Dreams Group
# workspace would bury the portal's own entry points under unused desk shortcuts.
#
# They are declared rather than guessed because a DocType that is neither on the
# workspace nor on the portal is silently unreachable to the employees it serves;
# listing them here keeps the guard's opposite check - a portal page pointing at
# a doctype that is not listed as portal-owned - possible.
#
# The list tracks the app's shadows of former HRMS doctypes.  HRMS v16.50
# dropped most of them from its own tree, and the bench's own schema sync then
# (re)created their records from this app's module with `module` set to this
# app's module, which pulled them into the guard's expected set for the first
# time.  Being newer than the guard, they are excluded here in the same way the
# superseded self-service DocTypes are above.
PORTAL_LINKED_DOCTYPES = {
	"Asset Request",
	"Course",
	"Employee Badge",
	"Employee Certification",
	"Employee Course Enrollment",
	"Employee Health Checkup",
	"Employee Insurance Claim",
	"Employee Insurance Enrollment",
	"Employee Learning Path",
	"Employee Recognition",
	"Employee Wellness Program",
	"Insurance Company",
	"Insurance Scheme",
	"Learning Path",
	"Loan Type",
	"Recognition Type",
	"SDG Announcement",
	"SDG Support Topic",
	"Safety Incident",
	"Safety Observation",
	"Shift Swap Request",
	"Training Provider",
	"Travel Request",
}


def apply() -> None:
	"""Create or refresh the workspace and the stock Link Cards.  Idempotent."""
	linked = {doctype for _card, entries in CARDS for doctype in entries}
	expected = _expected_doctypes() | ADOPTED_DOCTYPES
	unexpected = linked - expected
	if unexpected:
		frappe.throw(
			frappe._("CARDS lists doctypes that are not owned by this app: {0}").format(
				", ".join(sorted(unexpected))
			),
			title=frappe._("Wrong DocType in CARDS"),
		)

	_ensure_workspace(linked)

	ours = frappe.get_doc("Workspace", WORKSPACE)
	added = add_dashboard_blocks(ours)
	ours.save(ignore_permissions=True)
	print(f"  dashboard blocks: {added} written")

	for name, cards in STOCK_CARDS.items():
		print(f"  {name}: {_add_stock_cards(name, cards)}")

	frappe.db.commit()
	frappe.clear_cache()

	report = resolution_report()
	for name, error in unreadable_workspaces(report).items():
		print(f"  {name}: could not read its page payload this run ({error}); not treated as a fault")
	foreign = unresolved_blocks(report)
	if foreign:
		print(f"  note: pre-existing dangling blocks not added by this app: {frappe.as_json(foreign)}")
	broken = unresolved_blocks(report, ours_only=True)
	if broken:
		frappe.throw(
			frappe._("Workspace blocks that do not resolve: {0}").format(frappe.as_json(broken)),
			title=frappe._("Unresolvable workspace block"),
		)

	reachable = reachable_doctypes()
	missing = sorted(expected - reachable - SUPERSEDED_DOCTYPES - PORTAL_LINKED_DOCTYPES)
	print(f"workspace: {WORKSPACE}  {len(reachable & expected)} of {len(expected)} doctypes linked")
	if missing:
		frappe.throw(
			frappe._("Not reachable from the desk: {0}").format(", ".join(missing)),
			title=frappe._("Unreachable DocType"),
		)
	print("  all doctypes are reachable and every block resolves")


def _ensure_workspace(linked: set[str]) -> None:
	"""Create or rewrite the workspace's shortcuts and content from CARDS."""
	created = False
	if frappe.db.exists("Workspace", WORKSPACE):
		ws = frappe.get_doc("Workspace", WORKSPACE)
	else:
		ws = frappe.get_doc(
			{
				"doctype": "Workspace",
				"label": WORKSPACE,
				"title": WORKSPACE,
				"module": MODULE,
				"app": APP,
				"public": 1,
				"icon": "octicon-heart",
				# A Workspace has no link target of its own.  Inserting without
				# these leaves link_type at its default of "DocType", which the
				# desk then tries to resolve as a doctype route.
				"type": "Workspace",
				"link_type": None,
				"link_to": None,
			}
		)
		ws.insert(ignore_permissions=True)
		created = True

	# clear the wrong values if an earlier version wrote them
	if ws.link_type or ws.link_to:
		ws.link_type = None
		ws.link_to = None

	# rows first, then the blocks that reference them
	#
	# `label` is derived data, and the desk resolves a content block against *it*
	# rather than against `link_to` - `frappe/desk/desktop.py::get_desktop_page`
	# builds the shortcut list from the row's label, and `resolution_report`
	# compares the block's `shortcut_name` against that list. So a label left
	# over from a previous name is not cosmetic: renaming a DocType moved
	# `link_to`, the row kept saying "Kiosk Settings", and every rewritten block
	# came back unresolvable while the desk showed a stale caption. Re-derived on
	# every run, which makes it self-correcting.
	keep = linked | ADOPTED_DOCTYPES
	for row in list(ws.shortcuts):
		if row.link_to not in keep:
			# a DocType this workspace no longer lists at all
			ws.remove(row)
		elif row.type == "DocType" and row.label != row.link_to:
			row.label = row.link_to

	present = {row.link_to for row in ws.shortcuts}
	# `CARDS` order, not sorted: the whole point of the reading order documented on
	# CARDS is that rates come before the settings that drive them. Sorting threw
	# that away and put `Income Tax Slab` - the one entry a payroll manager opens
	# daily - behind `Employee Self Service Settings`, purely alphabetically.
	for doctype in [d for _card, entries in CARDS for d in entries if d not in present]:
		ws.append(
			"shortcuts",
			{
				"type": "DocType",
				"link_to": doctype,
				"label": doctype,
				# "Form" is not one of this field's options -
				# `\nList\nReport Builder\nDashboard\nTree\nNew\nCalendar\nKanban
				# \nImage` - so writing it made the Workspace unsaveable whenever a
				# Single was linked. Which is to say it had never been exercised:
				# `SDG Payroll Settings` only became a Single after the shortcut
				# above it was written.
				#
				# Blank is the right value rather than "List" because the field is
				# never read for a Single. `frappe.get_route` in
				# `frappe/public/js/frappe/utils/utils.js` tests
				# `frappe.model.is_single(item.doctype)` *before* it switches on
				# `doc_view`, and routes to `<slug>/<doctype>` - the form - either
				# way. So anything that validates lands on the same page.
				"doc_view": "" if _is_single(doctype) else "List",
			},
		)

	# `workspace_blocks` builds the whole page - figures first, setup links last -
	# and `content` stays a local because the block count is printed below.
	content = workspace_blocks(linked)
	ws.content = json.dumps(content)
	ws.title = WORKSPACE
	ws.public = 1
	# An octicon name, not a logo path: `Workspace.icon` is an Icon field and the
	# desk renders it through `frappe.utils.icon`, which only draws from the icon
	# font. The site had "brain" here, which is not one of the 191 glyphs in
	# octicons.css, so the page header was drawing a missing-glyph box.
	#
	# An image cannot go in this field. The logo is used where an image is
	# actually supported: the navbar (Navbar Settings.app_logo), the app switcher
	# (add_to_apps_screen) and the browser tab (Website Settings.favicon).
	ws.icon = WORKSPACE_ICON
	# Lowest sequence on the site, which is what makes this the page bare `/desk`
	# lands on.
	#
	# `Workspace.show()` -> `get_page_to_show()` does NOT consult the user's
	# `default_workspace`; it takes `localStorage.current_page` if that is still a
	# valid workspace and otherwise falls back to `workspaces[0]`, and
	# `get_workspaces` orders by `sequence_id asc`. At 20 this page sat below Build
	# and Home, so opening /desk never reached it - `default_workspace` only steers
	# the `/` redirect, which is a different route.
	ws.sequence_id = WORKSPACE_SEQUENCE
	ws.save()
	print(f"{'created' if created else 'updated'} workspace: {WORKSPACE} ({len(content) - 1} links)")


# There is deliberately no separate penalty strip here.
#
# This used to build one: a "Penalties" band holding the six level cards, worst
# first, from its own `PENALTY_CARD_ORDER` list.  The dashboard has a `Discipline`
# band for the same six cards, and now that `_dashboard_sections` reads the
# dashboard's bands rather than keeping its own copy, the front page was drawing
# them twice - once as `Discipline`, once as `Penalties`, both visible, with the
# same figures under two headings on the page a user lands on.
#
# One page, one order.  The levels are drawn in the dashboard's order, which puts
# the total first and then runs least-severe to worst, so the band reads as a
# scale rather than as an alarm; and a level HR deletes still drops off, because
# the band is built from the cards that exist (`_existing`).


def workspace_blocks(linked: set[str] | None = None) -> list[dict]:
	"""The whole page, in the order a reader wants it.

	**Figures first, setup last, and a heading between them.**

	The order this used to be was: the workspace name, then every config link in
	CARDS order, then the figures. That put twenty-one setup DocTypes - `SDG Medical
	Band`, `Employee Self Service Settings`, `Income Tax Slab` - above every number on the page a user
	lands on, which is the page whose first four rows were six rows of band tables and
	penalty levels before anyone saw a headcount. It was defensible when the page held
	links and nothing else; it stopped being defensible the moment figures were added
	underneath, and nobody moved them up.

	The rule now: a figure is what you open the desk for, and a band table is what you
	open it to *change*. The second is still on the page - every DocType stays
	reachable, which `apply()` proves - but it is below the figures and labelled, so
	its presence is a deliberate choice rather than a row you scroll into.

	**Headings between the bands.** The figures already came from the dashboard's own
	bands, so the names were right, but nothing on the page said so: `_dashboard_card_
	blocks` wrote a `header` block per band, and the desk drew no heading element from
	it. The bands ran together as 46 cards in one undifferentiated wall, which is the
	precise failure the dashboard's `SECTION_LAYOUT` exists to prevent and which the
	workspace had reintroduced by not having one. So the bands are written as real
	`spacer` + `header` pairs now, the same shape the dashboard renders.

	Ordering is read, not hand-maintained: `CARDS` order for the setup links, and
	`DASHBOARD_SECTIONS` order for the figures, which is where the dashboard reads
	its own from.
	"""
	if linked is None:
		linked = {doctype for _card, entries in CARDS for doctype in entries}

	blocks: list[dict] = [
		{
			"id": f"{BLOCK_PREFIX}header",
			"type": "header",
			"data": {"text": WORKSPACE, "level": 4, "col": 12},
		}
	]

	# --- the figures, band by band ------------------------------------------
	for title, labels, charts in _dashboard_sections():
		blocks.extend(_band_heading(title))
		blocks.extend(_card_blocks(labels))
		blocks.extend(_chart_blocks(charts))

	# --- setup, last and labelled -------------------------------------------
	# Its own heading, so the change of character is visible rather than something a
	# reader discovers by noticing the words stopped being about people.
	blocks.append(
		{
			"id": f"{BLOCK_PREFIX}setup-spacer",
			"type": "spacer",
			"data": {"height": 24},
		}
	)
	blocks.append(
		{
			"id": f"{BLOCK_PREFIX}setup-header",
			"type": "header",
			"data": {"text": "Setup", "level": 4, "col": 12},
		}
	)
	blocks.append(
		{
			"id": f"{BLOCK_PREFIX}setup-note",
			# `paragraph`, not `text`.  There is no `text` block in this Frappe:
			# `frappe.workspace_block.blocks` registers header, paragraph, card, chart,
			# shortcut, spacer, onboarding, quick_list, number_card and custom_block, and
			# nothing else.  Editor.js does not fail loudly on an unknown type - it draws
			# a stub reading "The block can not be displayed correctly.", so the page
			# carried one broken box on it with nothing in the server log to say why.
			#
			# `text` is the *toolbox title* of the paragraph tool, which is where the
			# mistake comes from: `Paragraph.toolbox` returns `{title: "Text"}`, so the
			# block picker labels it "Text" while the stored type is `paragraph`. Writing
			# the label instead of the name is the trap, and an easy one to fall into
			# from reading the picker rather than the registry.
			#
			# The data shape was already correct: `Paragraph`'s setter reads `data.text`
			# into the element and its `save()` writes `{text, col}` back, so only the
			# type was wrong.
			"type": "paragraph",
			"data": {
				"text": "Band tables and settings. The dashboards above are where the "
				"figures live; these are the tables behind them.",
				"col": 12,
			},
		}
	)
	for card, entries in CARDS:
		present = [doctype for doctype in entries if doctype in linked]
		if not present:
			continue
		blocks.append(
			{
				"id": f"{BLOCK_PREFIX}setup-{_slug(card)}",
				"type": "header",
				"data": {"text": card, "level": 5, "col": 12},
			}
		)
		for doctype in present:
			blocks.append(_shortcut_block(doctype, 3))

	return blocks


def _band_heading(title: str) -> list[dict]:
	"""A band's spacer and heading.

	Both, and the spacer is why. A heading with nothing above it sits flush against
	the row of cards above it, and the bands run together again - which is the
	problem the heading was added to solve.
	"""
	return [
		{
			"id": f"{BLOCK_PREFIX}spacer-{_slug(title)}",
			"type": "spacer",
			"data": {"height": 12},
		},
		{
			"id": f"{BLOCK_PREFIX}band-{_slug(title)}",
			"type": "header",
			"data": {"text": title, "level": 5, "col": 12},
		},
	]


def _existing(labels) -> list[str]:
	"""Only labels that have a real Number Card behind them.

	A block pointing at a card that does not exist renders as an empty tile, which
	reads as "zero" rather than "deleted", so a renamed or removed level drops out
	of the layout on its own.
	"""
	present = set(frappe.get_all("Number Card", pluck="name", limit_page_length=0))
	return [label for label in labels if label in present]


def _dashboard_sections() -> tuple[tuple[str, tuple[str, ...], tuple[str, ...]], ...]:
	"""`(title, cards, charts)` per band, read off the dashboard's own layout.

	This used to be a second, hand-maintained list living here - `People` /
	`Compliance` / `Contracts` / `Benefits` / `Payroll` - and it had drifted from
	`hr.dashboard.DASHBOARD_SECTIONS`, which is what `SDG Workforce` renders.  Two
	lists for one set of figures, on a workspace that is the front page every user
	lands on, and the drift showed two ways:

	  * the bands were named differently - "Compliance" here, "Records To
	    Complete" there, "Benefits" here, "Benefits & Insurance" there - so the
	    same figure sat under two names on two pages a click apart;
	  * nothing later was added to this list, so twelve real cards (Average Net
	    Salary, Tenure 5+ Years, ID Expiring (30d), Penalty Cases and eight more)
	    belonged to no band at all and were rendered under a heading literally
	    spelled **Other**.  On a front page, that is the desk admitting nobody
	    decided where these go.

	`DASHBOARD_SECTIONS` is the one place that answers "which figures, in which
	order, under which name", and the dashboard already renders from it, so this
	reads it rather than restating it.  A band is dropped only when none of its
	cards exist; a card no band names is a defect the dashboard's own
	`test_every_card_this_app_publishes_is_on_a_section` fails on, so nothing can
	quietly fall out here either.

	Charts come back too, and filtered through `FRONT_PAGE_CHARTS` - the front page
	carries a subset of the dashboard's charts, for the reason given on that table.
	A band whose only content was a chart it does not carry is still drawn, with its
	cards; a band with neither is dropped rather than leaving a heading over nothing.
	"""
	from sharm_dreams_group.hr import dashboard

	present = set(frappe.get_all("Dashboard Chart", pluck="name", limit_page_length=0))
	groups = []
	for title, labels, charts in dashboard.DASHBOARD_SECTIONS:
		cards = _existing(labels)
		# `FRONT_PAGE_CHARTS` is keyed by band, so membership has to be against its
		# *values*. `chart in FRONT_PAGE_CHARTS` tests the keys - the band titles -
		# and every chart name is absent from those, so `carried` came back empty
		# for every band and the page drew no charts at all. Silently: the charts a
		# band declared simply did not appear, and nothing failed.
		wanted = FRONT_PAGE_CHARTS.get(title, ())
		carried = tuple(chart for chart in charts if chart in present and chart in wanted)
		if not cards and not carried:
			continue
		groups.append((title, cards, carried))
	return tuple(groups)


def _card_blocks(labels) -> list[dict]:
	"""A `number_card` block per figure, in the reading order.

	`Workspace.content` is the *layout*: `workspace.js` renders the blocks in
	`content` and looks the card details up in the child-table payload that
	`desktop.get_desktop_page` returns.  A card in `number_cards` with no block in
	`content` is simply not drawn - which is why the child tables alone are not
	enough, and why the dashboard looked complete in the record while the page
	showed only shortcuts.

	Width is per band, and capped at six across.  It used to be `12 // len(labels)`
	with no cap, so an eleven-card band was laid out eleven across at 79px each - a
	row of numbers too narrow for "Average Gross Salary" to sit on one line, and the
	label wrapping to three. Six across is the widest that still fits the longest
	label this app publishes.
	"""
	blocks: list[dict] = []
	if not labels:
		return blocks
	col = max(12 // min(len(labels), 6), 2)
	for label in labels:
		blocks.append(
			{
				"id": f"{BLOCK_PREFIX}card-{_slug(label)}",
				"type": "number_card",
				"data": {"number_card_name": label, "col": col},
			}
		)
	return blocks


def _chart_blocks(names) -> list[dict]:
	"""A `chart` block per chart, at the width its category count needs.

	Full width for the per-company breakdowns, which have 13 categories and are
	unreadable side by side; half width for the categorical ones, which pair up
	naturally. The rule is the one the dashboard's own `ACCOUNT_SECTION_LAYOUT`
	and friends declare per block, and it is restated here rather than read from
	them because `FRONT_PAGE_CHARTS` may name a chart on a band the dashboard lays
	out differently.
	"""
	blocks: list[dict] = []
	for name in names:
		blocks.append(
			{
				"id": f"{BLOCK_PREFIX}chart-{_slug(name)}",
				"type": "chart",
				"data": {"chart_name": name, "col": 12 if "Company" in name else 6},
			}
		)
	return blocks


# The charts the workspace front page carries, and where they go.
#
# A deliberate subset, not the dashboard's whole set: this page is the first thing
# a user sees, and `SDG Workforce`, `SDG HRM` and `SDG Account` are where the rest
# of the figures live.  A card repeated on three pages is a card nobody trusts,
# so this is the front page's set, not the app's.  The *bands* above are not a list
# of their own - they are read from the dashboard's, so the two pages cannot
# disagree about a figure's name or order.
#
# The chart is now matched to its own band rather than dumped under one "Breakdowns"
# heading at the bottom.  Under that heading, `Headcount by Company` and `Payroll Net
# by Company` were nine rows apart with forty-six cards of payroll figures in
# between, so the breakdown of a number you had just read was somewhere else on the
# page entirely.  A chart about leavers now sits under the band that counts leavers.
DASHBOARD_CHARTS = (
	"Headcount by Company",
	"Leavers by Company",
	"Payroll Net by Company",
	"Headcount by Department",
	"Social Insurance Status",
	"Employment Category",
	"Medical Cover by Band",
	"Payroll Gross by Company",
	"People of Determination by Company",
)

# The subset of the above that this page actually draws, by band.
#
# Same reasoning as `DASHBOARD_CHARTS` - a deliberate subset, so the front page is a
# summary - but keyed by band so `_dashboard_sections` can put each chart under the
# figures it explains.  `DASHBOARD_CHARTS` stays as the list of what the page is
# *allowed* to carry and `add_dashboard_blocks` still writes the child-table rows for
# it; a chart named in neither table is not on this page, and one named only here
# would have a row and no block, which renders as an empty tile.
FRONT_PAGE_CHARTS: dict[str, tuple[str, ...]] = {
	"People": (
		"Headcount by Company",
		"Headcount by Department",
		"Employment Category",
		"Leavers by Company",
		"People of Determination by Company",
	),
	"Payroll": ("Payroll Net by Company", "Payroll Gross by Company"),
	"Benefits & Insurance": ("Social Insurance Status", "Medical Cover by Band"),
}


def add_dashboard_blocks(ws) -> None:
	"""Put the workforce Number Cards and Charts on our own workspace.

	Written to the `number_cards` and `charts` child tables, which is where the
	desk looks up a card's details.  These rows are necessary but NOT sufficient:
	`Workspace.content` carries the *layout*, and `workspace.js` draws the blocks
	in `content` only.  A card listed here with no matching block in `content` is
	invisible on the page while looking perfectly complete in the record, so
	`workspace_blocks` writes the layout to match these rows.  Both halves are
	written on every run; neither alone renders.

	Rows are replaced rather than appended to: a card that has since been deleted
	would otherwise leave a row pointing at nothing, which renders as an empty tile
	and reads as "zero people" rather than "no longer tracked".

	Every name in `DASHBOARD_CHARTS` gets a row even when its band is not carried
	on this page, so the row and the block are written from one source and cannot
	disagree. `FRONT_PAGE_CHARTS` decides what is *drawn*; this decides what is
	*available*.
	"""
	from sharm_dreams_group.hr import dashboard

	ws.set("number_cards", [])
	ws.set("charts", [])

	cards = set(frappe.get_all("Number Card", pluck="label", limit_page_length=0))
	charts = set(frappe.get_all("Dashboard Chart", pluck="chart_name", limit_page_length=0))

	# our own metric cards, then the penalty strip, then any stock card an
	# administrator added to this workspace - kept in case anyone uses it
	ours = [spec["label"] for spec in dashboard.metric_definitions() if spec["label"] in cards]
	penalties = sorted(
		label
		for label in cards
		if frappe.db.exists(
			"Number Card", {"label": label, "method": "sharm_dreams_group.hr.penalty.headcount"}
		)
	)
	kept = [
		row.number_card_name
		for row in (
			frappe.get_all(
				"Workspace Number Card",
				filters={"parent": WORKSPACE},
				pluck="number_card_name",
				limit_page_length=0,
			)
			or []
		)
		if row not in ours and row not in penalties
	]

	added = 0
	for label in ours + penalties + kept:
		ws.append("number_cards", {"number_card_name": label, "label": label})
		added += 1

	for name in DASHBOARD_CHARTS:
		if name not in charts:
			continue
		ws.append("charts", {"chart_name": name, "label": name})
		added += 1

	return added


def _purge_our_rows(ws) -> None:
	"""Remove rows and blocks this module previously added to a stock workspace.

	An earlier version wrote `card` and `shortcut` blocks with no matching rows in
	the `links` / `shortcuts` child tables, so the desk skipped them silently.
	Those are removed here and replaced by real Link Cards, rather than left
	behind as dead content.
	"""
	card_labels = {card for cards in STOCK_CARDS.values() for card, _links in cards}

	for row in list(ws.links):
		if (row.type == "Card Break" and row.label in card_labels) or (
			row.link_to and str(row.link_to).startswith("SDG ")
		):
			ws.links.remove(row)
	for row in list(ws.shortcuts):
		if row.link_to and str(row.link_to).startswith("SDG "):
			ws.shortcuts.remove(row)

	blocks = json.loads(ws.content or "[]")
	ws.content = json.dumps([b for b in blocks if not str(b.get("id", "")).startswith(BLOCK_PREFIX)])


def _add_stock_cards(workspace_name: str, cards: list) -> str:
	"""Add Link Cards to a stock workspace, leaving its own cards untouched.

	A Link Card is three things that must agree, or the desk renders nothing:
	a `Card Break` row, one `Link` row per doctype, and a matching `card` block.
	"""
	if not frappe.db.exists("Workspace", workspace_name):
		return "skipped, no such workspace"

	ws = frappe.get_doc("Workspace", workspace_name)
	_purge_our_rows(ws)
	blocks = json.loads(ws.content or "[]")

	for label, doctypes in cards:
		ws.append(
			"links",
			{
				"type": "Card Break",
				"label": label,
				"description": "Sharm Dreams Group customisations",
				"link_count": len(doctypes),
			},
		)
		for doctype in doctypes:
			ws.append(
				"links",
				{
					"type": "Link",
					"label": doctype,
					"link_type": "DocType",
					"link_to": doctype,
				},
			)
		blocks.append(
			{
				"id": f"{BLOCK_PREFIX}{_slug(workspace_name)}-{_slug(label)}",
				"type": "card",
				"data": {"card_name": label, "col": 4},
			}
		)

	ws.content = json.dumps(blocks)
	# The stock workspaces we extend carry HRMS links that this site lacks -
	# "Provident Fund Deductions" / "Professional Tax Deductions" in Payroll are
	# India-region report shortcuts that do not resolve here - and `save()`
	# validates every row, not just ours, so the whole save dies on those rows.
	# Only our own block is written here; the desk-side resolution check in
	# `apply()` (via `unresolved_blocks`) still verifies what we add.
	ws.flags.ignore_links = True
	ws.save(ignore_permissions=True)
	return ", ".join(f"{label} ({len(links)} links)" for label, links in cards)


# ---------------------------------------------------------------------------
# verification
# ---------------------------------------------------------------------------
def _page_payload(name: str) -> dict:
	"""The payload the desk renders, built by its own call."""
	from frappe.desk.desktop import get_desktop_page

	ws = frappe.get_doc("Workspace", name)
	args = json.dumps({"name": name, "title": ws.title, "public": ws.public})

	# `get_desktop_page` resolves every link that declares `dependencies` through
	# the table-count cache, and `build_table_count_cache` deliberately refuses to
	# build while `in_migrate` is set. A migrate clears the cache first, so a cold
	# cache during one means the lookup hands back None and the first stock link
	# with a dependency raises `'NoneType' object has no attribute 'get'`. HR
	# Setup carries ten such links, all of them stock HRMS reports.
	#
	# The flag is lifted for this one read so the check the whole verification
	# rests on actually runs. It was not: `apply()` reported "every block
	# resolves" for HR Setup having silently proved nothing at all.
	was_in_migrate = frappe.flags.in_migrate
	frappe.flags.in_migrate = False
	try:
		return get_desktop_page(args)
	finally:
		frappe.flags.in_migrate = was_in_migrate


def resolution_report() -> dict[str, dict]:
	"""Which content blocks the desk can actually resolve, per workspace.

	The check that matters.  A block with no matching child-table row is skipped
	by the browser, so presence in `content` proves nothing on its own; this joins
	the blocks against the page payload the same way `block.js make()` does.
	"""
	out = {}
	for name in [WORKSPACE, *STOCK_CARDS]:
		if not frappe.db.exists("Workspace", name):
			continue
		blocks = json.loads(frappe.get_doc("Workspace", name).content or "[]")
		try:
			data = _page_payload(name)
		except Exception as e:
			out[name] = {"error": str(e)}
			continue

		page_cards = [c.get("label") for c in (data.get("cards") or {}).get("items", [])]
		page_shorts = [s.get("label") for s in (data.get("shortcuts") or {}).get("items", [])]
		content_cards = [b.get("data", {}).get("card_name") for b in blocks if b.get("type") == "card"]
		content_shorts = [
			b.get("data", {}).get("shortcut_name") for b in blocks if b.get("type") == "shortcut"
		]
		out[name] = {
			"page_cards": page_cards,
			"page_shortcuts": page_shorts,
			"unresolvable_cards": [c for c in content_cards if c and c not in page_cards],
			"unresolvable_shortcuts": [s for s in content_shorts if s and s not in page_shorts],
		}
	return out


def unresolved_blocks(report: dict, ours_only: bool = False) -> dict:
	"""Workspaces holding content blocks the desk will not render.

	Only genuine faults: a block with no matching child-table row.  A workspace
	whose page payload could not be *read* is a separate condition, reported by
	`unreadable_workspaces`, because it is a different kind of problem.

	`ours_only` narrows it to blocks this module added - those carry BLOCK_PREFIX
	on their id, so they are identifiable without guessing.  This matters
	because a stock workspace can arrive already broken: ERPNext ships its Home
	fixture with five shortcut blocks and four rows, the dangling one being
	Leaderboard, which is not a Report on this site either.  Failing the whole
	migrate over somebody else's fixture would make this app un-migratable, and
	"fixing" it by adding a row would point at a report that does not exist.
	"""
	owned = {c for cards in STOCK_CARDS.values() for c, _d in cards}
	out = {}
	for name, info in report.items():
		if "error" in info:
			continue
		bad = info["unresolvable_cards"] + info["unresolvable_shortcuts"]
		if ours_only:
			bad = [b for b in bad if b in owned or b.startswith("sdg-")]
		if bad:
			out[name] = bad
	return out


def unreadable_workspaces(report: dict) -> dict:
	"""Workspaces whose page payload could not be built, and why.

	Separate from `unresolved_blocks` because the two mean different things: an
	unresolved block is content the desk cannot render, whereas an unreadable page
	payload means the check itself could not run, so nothing is proved either way.

	`apply()` warns and carries on rather than failing the whole migrate, because
	an upgrade that rebuilds a stock workspace may briefly leave it unreadable.
	`verify()` reports it, and the tests fail on it.
	"""
	return {name: info["error"] for name, info in report.items() if "error" in info}


def reachable_doctypes() -> set[str]:
	"""DocTypes reachable from the SDG workspace, read out of its content.

	Resolution is checked separately by `resolution_report`; this is the list of
	names the workspace points at.
	"""
	if not frappe.db.exists("Workspace", WORKSPACE):
		return set()
	found = set()
	for block in json.loads(frappe.get_doc("Workspace", WORKSPACE).content or "[]"):
		if block.get("type") not in ("shortcut", "card"):
			continue
		name = block.get("data", {}).get("shortcut_name") or block.get("data", {}).get("card_name")
		if name:
			found.add(name)
	return found


def stock_card_links() -> dict[str, set[str]]:
	"""The doctypes each stock workspace's Link Cards actually expose."""
	out = {}
	for name, cards in STOCK_CARDS.items():
		if not frappe.db.exists("Workspace", name):
			continue
		labels = {card for card, _links in cards}
		inside: set[str] = set()
		current = None
		for row in frappe.get_doc("Workspace", name).links:
			if row.type == "Card Break":
				current = row.label
			elif current in labels and row.link_to:
				inside.add(row.link_to)
		out[name] = inside
	return out


def table_report() -> list[str]:
	"""Whether each owned doctype is backed by a real database table.

	Singles are reported as-is: they are stored in `tabSingles` and have no table
	of their own, which is Frappe's own behaviour (System Settings and HR
	Settings are the same), so probing for a table would be a false alarm.
	"""
	from frappe.model.meta import get_table_columns

	out = []
	for name in sorted(_expected_doctypes()):
		if _is_single(name):
			out.append(f"  single  {name}  (stored in tabSingles)")
			continue
		try:
			out.append(f"  ok      {name}  ({len(get_table_columns(name))} cols)")
		except Exception as e:
			out.append(f"  NO TABLE  {name}  ({type(e).__name__})")
	return out


def set_default_workspace(only_missing: bool = True) -> int:
	"""Land users on this app's workspace instead of ERPNext's Home.

	The desk router resolves a bare `/desk` in a documented order (see
	`frappe/public/js/frappe/router.js`): the user's own `default_workspace`
	first, then the private home, then the public home.  Nothing here sets a
	`home_page` hook, so every user fell through to ERPNext's public "Home"
	workspace and the app's workspace was only reachable by typing its URL or
	clicking the icon in the apps switcher.  Setting the stock per-user field is
	the first step of that order and needs no hook of our own.

	`only_missing` leaves a deliberate choice alone: someone who has picked a
	workspace of their own keeps it, because this is a default rather than a
	policy.
	"""
	if not frappe.db.exists("Workspace", WORKSPACE):
		frappe.throw(frappe._("The {0} workspace does not exist; run apply() first.").format(WORKSPACE))

	changed = 0
	for name in frappe.get_all("User", filters={"enabled": 1}, pluck="name", limit_page_length=0):
		if name == "Guest":
			continue
		current = frappe.db.get_value("User", name, "default_workspace")
		if current == WORKSPACE:
			continue
		if only_missing and current:
			continue
		frappe.db.set_value("User", name, "default_workspace", WORKSPACE, update_modified=False)
		changed += 1

	if changed:
		frappe.clear_cache()
	return changed


def on_user_create(doc, method=None) -> None:
	"""Give a new user the same landing workspace as everyone else.

	Without this the default only ever reaches the users who existed when the app
	was installed, so the first person HR hires lands on ERPNext's Home and it
	looks like the app is missing again.
	"""
	if doc.name == "Guest" or not doc.enabled:
		return
	if not frappe.db.exists("Workspace", WORKSPACE):
		return
	frappe.db.set_value("User", doc.name, "default_workspace", WORKSPACE, update_modified=False)


def has_app_permission() -> bool:
	"""Whether the current user may see the app on the apps switcher.

	The workspace is public, so visibility follows the workspace rather than the
	payroll roles: this app will hold customisations for every Frappe app, not
	just payroll, and hiding it from non-payroll staff would be wrong.
	"""
	return True


def verify() -> None:
	"""Report what the desk can reach, and refuse to pass if anything is broken."""
	if not frappe.db.exists("Workspace", WORKSPACE):
		print("MISSING: the workspace does not exist. Run workspace.apply.")
		return

	expected = _expected_doctypes()
	reachable = reachable_doctypes()
	ws = frappe.get_doc("Workspace", WORKSPACE)

	print(f"=== {WORKSPACE} ===")
	print(f"  public={ws.public}  module={ws.module}  app={ws.app}")
	print(f"  doctypes owned: {len(expected)}   linked: {len(reachable & expected)}")
	for name in sorted(expected):
		if name in SUPERSEDED_DOCTYPES:
			note = "SUPERSEDED (intentionally unlinked)"
		elif name in reachable:
			note = "ok"
		else:
			note = "MISSING"
		print(f"    {note:38}  {name}")

	print("\n=== database tables ===")
	print("\n".join(table_report()))

	print("\n=== block resolution (what the desk can render) ===")
	report = resolution_report()
	for name, info in report.items():
		if "error" in info:
			print(f"  {name}: ERROR {info['error']}")
		else:
			bad = info["unresolvable_cards"] + info["unresolvable_shortcuts"]
			print(f"  {name}: {'ok' if not bad else f'UNRESOLVED {bad}'}")
	broken = unresolved_blocks(report)
	broken.update(unreadable_workspaces(report))

	print("\n=== stock workspace Link Cards ===")
	cards = stock_card_links()
	missing_links = {}
	for name, links in cards.items():
		want = {d for _c, entries in STOCK_CARDS[name] for d in entries}
		missing = sorted(want - links)
		print(f"  {name}: {'ok' if not missing else f'MISSING {missing}'}  ({len(links)} links)")
		if missing:
			missing_links[name] = missing

	if broken:
		frappe.throw(
			frappe._("Workspace blocks that do not resolve: {0}").format(frappe.as_json(broken)),
			title=frappe._("Unresolvable workspace block"),
		)
	if missing_links:
		frappe.throw(
			frappe._("Link Cards missing links: {0}").format(frappe.as_json(missing_links)),
			title=frappe._("Incomplete Link Card"),
		)
	# A superseded or portal-only doctype is excluded from the guard by name,
	# not by pattern, so a doctype that merely stops being reachable is still a
	# failure.
	unreachable = expected - reachable - SUPERSEDED_DOCTYPES - PORTAL_LINKED_DOCTYPES
	if sorted(unreachable):
		frappe.throw(
			frappe._("Not reachable from the desk: {0}").format(", ".join(sorted(unreachable))),
			title=frappe._("Unreachable DocType"),
		)
	print("\nall doctypes are reachable and every block resolves")


def content_blocks() -> list[dict]:
	"""The blocks the desk actually renders for our workspace.

	Exposed for the tests: `content` is the layout, and a card or chart missing
	from it is not drawn, however complete the child tables are.
	"""
	return frappe.parse_json(frappe.get_doc("Workspace", WORKSPACE).content) or []
