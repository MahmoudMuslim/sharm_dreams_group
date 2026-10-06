# Copyright (c) 2026, Sharm Dreams Group and contributors
# For license information, please see license.txt

"""Attach the staged employee photos to `Employee.image`.

The photos are named after the person, not by employee number, so the matching
happens in `tools/build_photo_manifest.py` on the host, where the files live, and
lands here as `seed_data/employee_photos.json`.  This module only does the
Frappe side: create a File, attach it, point `Employee.image` at it.

Nothing is matched here.  The manifest records, for every photo, the method that
matched it (`exact`, `subset` or `near_exact`) and its score, and a photo whose
match is weaker is left in the review CSV for a human rather than guessed at -
a photo on the wrong employee record is worse than no photo at all.

Run:
    bench --site <site> execute sharm_dreams_group.hr.photos.apply
    bench --site <site> execute sharm_dreams_group.hr.photos.plan
    bench --site <site> execute sharm_dreams_group.hr.photos.attach --kwargs "{'employee_number': 'H0003', 'staged_path': '...'}"
"""

from __future__ import annotations

import json
import pathlib

import frappe
from frappe.utils.file_manager import save_file

from sharm_dreams_group.hr.employee import save_employee

MANIFEST = pathlib.Path(__file__).resolve().parent.parent.parent / "seed_data" / "employee_photos.json"

# The bench runs in a container that cannot see /home/mahmoud, so the tool
# copies matched photos here before this module reads them.
# .../<bench>/import_staging/photos.  parents[2] is the app dir, so the bench
# root is two above it.  The tool stages there because the container can see the
# bench but not /home/mahmoud.
STAGING_ROOT = pathlib.Path(__file__).resolve().parents[4] / "import_staging" / "photos"

# The stock Attach Image field on Employee.  `image` rather than `user_image`:
# these are staff photos on the employee record, not avatars for a login.
IMAGE_FIELD = "image"

# Below these a match is not attached.  Mirrors the tool's thresholds; anything
# weaker arrives in the manifest's review list instead.
ACCEPTED_METHODS = ("exact", "subset", "near_exact")


def load_manifest() -> dict:
	if not MANIFEST.exists():
		raise FileNotFoundError(
			f"{MANIFEST} not found. Run tools/build_photo_manifest.py --stage on the host first."
		)
	return json.loads(MANIFEST.read_text())


def _missing_mandatory(employee_number: str) -> list[str]:
	"""Mandatory fields this employee cannot satisfy, for reporting.

	Known to be four employees with no gender and one with no joining date, all
	of them gaps in the source workbooks rather than import failures.
	"""
	doc = frappe.get_doc("Employee", employee_number)
	names = []
	for field in doc.meta.fields:
		if field.reqd and not doc.get(field.fieldname):
			names.append(field.fieldname)
	return names


def _staged_path(row: dict) -> pathlib.Path:
	"""Where the tool put the file, for this app's notion of a path."""
	name = pathlib.Path(row["staged_path"]).name if row.get("staged_path") else ""
	if not name:
		# fall back to the conventional name: <employee_number><suffix>
		name = f"{row['employee_number']}{pathlib.Path(row['source_file']).suffix.lower()}"
	return STAGING_ROOT / name


def build_plan() -> dict:
	"""Decide what would be attached.  Writes nothing."""
	manifest = load_manifest()
	attached, skipped, errors = [], [], []

	seen: set[str] = set()
	for row in manifest["matched"]:
		who = row["employee_number"]
		path = _staged_path(row)

		if row["method"] not in ACCEPTED_METHODS:
			skipped.append((who, row["source_file"], f"method {row['method']} is not accepted"))
			continue
		if who in seen:
			skipped.append((who, row["source_file"], "a photo is already matched to this employee"))
			continue
		if not path.exists():
			errors.append(f"{who}: staged photo missing at {path}")
			continue
		if not frappe.db.exists("Employee", who):
			errors.append(f"{who}: no such employee")
			continue
		if frappe.db.get_value("Employee", who, IMAGE_FIELD):
			skipped.append((who, row["source_file"], "employee already has a photo"))
			continue
		seen.add(who)
		attached.append(
			{
				"employee_number": who,
				"path": str(path),
				"method": row["method"],
				"missing_mandatory": _missing_mandatory(who),
			}
		)

	return {"attached": attached, "skipped": skipped, "errors": errors, "manifest": manifest}


def report(result: dict) -> None:
	manifest = result["manifest"]
	print("=== employee photo import ===")
	print(f"  photos on disk      : {manifest['summary']['photos']}")
	print(f"  matched by the tool : {manifest['summary']['matched']}  {manifest['summary']['by_method']}")
	print(f"  to attach           : {len(result['attached'])}")
	for row in result["attached"]:
		note = ""
		if row.get("missing_mandatory"):
			note = f"  (missing mandatory: {', '.join(row['missing_mandatory'])})"
		print(f"     {row['employee_number']:8} {row['method']:11} {pathlib.Path(row['path']).name}{note}")
	if result["skipped"]:
		print(f"\n  skipped             : {len(result['skipped'])}")
		for who, source, why in result["skipped"]:
			print(f"     {who:8} {source:34} {why}")
	for group, key in (("ambiguous", "ambiguous"), ("duplicate", "duplicate_employee")):
		rows = manifest.get(key) or []
		if rows:
			print(f"\n  {group:19} : {len(rows)}")
			for row in rows[:8]:
				print(f"     {row['file']:34} {row['reason']}")
	if result["errors"]:
		print(f"\n  ERRORS ({len(result['errors'])}) - nothing written")
		for error in result["errors"]:
			print(f"     {error}")
	else:
		print("\nno errors")


def plan() -> None:
	result = build_plan()
	report(result)
	frappe.db.rollback()


def _attach_one(employee_number: str, path: pathlib.Path) -> None:
	"""Create the File and point the Employee's image field at it.

	Uses `frappe.utils.file_manager.save_file` rather than `File.save_file`,
	which reads its `content` argument as a *site-relative* path and would look
	for the photo in the site's own files directory.
	"""
	with open(path, "rb") as handle:
		file = save_file(
			path.name,
			handle.read(),
			"Employee",
			employee_number,
			df=IMAGE_FIELD,
			is_private=1,
		)
	# Five imported employees are missing a mandatory field the source does not
	# carry (four have no gender, H0141 no joining date).  That gap is reported
	# separately and is not the photo's business, so it must not block attaching
	# one; those employees still cannot be saved from the desk until HR fills it
	# in, which is a pre-existing condition rather than something this import
	# caused.
	save_employee(employee_number, {IMAGE_FIELD: file.file_url}, ignore_mandatory=True)


def apply() -> None:
	"""Attach the planned photos.  Refuses to write anything if the plan has errors."""
	result = build_plan()
	if result["errors"]:
		report(result)
		frappe.throw(
			f"{len(result['errors'])} error(s) in the photo plan; nothing was written.",
			title=frappe._("Photo import blocked"),
		)

	# one transaction: a failure part-way must not leave half the staff with a
	# photo and the other half without
	for row in result["attached"]:
		_attach_one(row["employee_number"], pathlib.Path(row["path"]))
	frappe.db.commit()
	frappe.clear_cache()

	total = frappe.db.count("Employee", {IMAGE_FIELD: ("is", "set")})
	print(f"\nattached {len(result['attached'])} photos")
	print(f"  employees with a photo now: {total} of {frappe.db.count('Employee')}")


def attach(employee_number: str, staged_path: str) -> None:
	"""Attach one photo by hand, for the cases the tool will not guess at.

	`staged_path` is a path under the staging directory, or any file the bench
	container can read.
	"""
	path = pathlib.Path(staged_path)
	if not path.is_absolute():
		path = STAGING_ROOT / staged_path
	if not path.exists():
		frappe.throw(frappe._("No such file: {0}").format(path), title=frappe._("Photo not found"))
	if not frappe.db.exists("Employee", employee_number):
		frappe.throw(
			frappe._("No such employee: {0}").format(employee_number), title=frappe._("Unknown employee")
		)
	_attach_one(employee_number, path)
	frappe.db.commit()
	frappe.clear_cache()
	print(f"attached {path.name} to {employee_number}")


def report_coverage() -> None:
	"""How many of the master-marked employees now have a photo."""
	from sharm_dreams_group.hr.importer import load_fixture

	fixture = load_fixture()
	expected = {r["employee_number"] for r in fixture["employees"] if r.get("has_photo_on_file")}
	with_photo = set(
		frappe.get_all("Employee", filters={IMAGE_FIELD: ("is", "set")}, pluck="name", limit_page_length=0)
	)
	print("=== photo coverage ===")
	print(f"  master marks a photo on file : {len(expected)}")
	print(f"  have a photo in Frappe       : {len(with_photo & expected)}")
	print(f"  still missing                : {len(expected - with_photo)}")
	print(f"  photos on employees the master does not list: {len(with_photo - expected)}")
