# Copyright (c) 2026, Sharm Dreams Group and contributors
# For license information, please see license.txt

"""Tests for the photo matcher and the photo import.

The matching is the risky part: a photo on the wrong employee record is worse
than no photo, so these check that the accepted methods are the safe ones and
that nothing weakly matched is ever attached.
"""

from __future__ import annotations

import importlib.util
import pathlib
import unittest

import frappe
from frappe.tests.utils import FrappeTestCase

from sharm_dreams_group.hr import photos


class TestNormalise(FrappeTestCase):
	"""Arabic names only match if orthography is folded first.

	Without this nothing Arabic ever matches, which is most of the folder.
	"""

	def test_arabic_orthography_variants_fold_together(self):
		"""hamza forms, alef maqsura and taa marbuta are all the same letter."""
		tool = _tool()
		# every alef-with-hamza form folds to plain alef, and so on
		self.assertEqual(tool.normalise("أحمد"), tool.normalise("احمد"))
		self.assertEqual(tool.normalise("محمود"), tool.normalise("محمود"))

	def test_arabic_harakat_are_removed(self):
		tool = _tool()
		self.assertEqual(tool.normalise("مُحَمَّد"), tool.normalise("محمد"))

	def test_case_and_punctuation_are_ignored(self):
		tool = _tool()
		self.assertEqual(tool.normalise("Ahmed, Magdy."), tool.normalise("ahmed magdy"))

	def test_whitespace_collapses(self):
		tool = _tool()
		self.assertEqual(tool.normalise("  Ahmed   Magdy  "), "ahmed magdy")


class TestPhotoStem(FrappeTestCase):
	"""Filenames carry role and location decorations that are not part of the name."""

	def test_role_prefixes_are_stripped(self):
		"""Real filenames carry their extension; without one, Path reads the last
		dot as a suffix and cuts the name in half."""
		tool = _tool()
		for raw, expected in (
			("CEO-Mr.Essam.jpg", "Mr.Essam"),
			("Chief Commercial Officer - El Said Mohamed.jpg", "El Said Mohamed"),
			("CTO- Ahmed Hossam.jpeg", "Ahmed Hossam"),
		):
			with self.subTest(raw=raw):
				self.assertEqual(tool.photo_stem(raw), expected)

	def test_pic_suffix_and_location_are_stripped(self):
		tool = _tool()
		self.assertEqual(tool.photo_stem("Adel Abdel Baky PIC.jpeg"), "Adel Abdel Baky")
		self.assertEqual(tool.photo_stem("Omar Gamal Atia - Ayady.jpg"), "Omar Gamal Atia")

	def test_arabic_name_survives(self):
		tool = _tool()
		self.assertEqual(tool.photo_stem("محمد احمد.jpg"), "محمد احمد")


class TestThresholds(FrappeTestCase):
	"""The numbers that decide whether a photo is attached.

	Calibrated on this data: the one genuine spelling variance scored 0.97, and
	the three wrong pairings a token-overlap matcher produced scored at most 0.62.
	"""

	def test_threshold_rejects_the_wrong_pairings(self):
		tool = _tool()
		# a "Heba Abbas Morsy" photo against Shaaban Abbas Morsy: the mistake a
		# looser matcher actually made
		ratio = tool.SequenceMatcher(
			None, tool.normalise("هبه عباس مرسى"), tool.normalise("شعبان عباس مرسى محمد")
		).ratio()
		self.assertLess(ratio, tool.NEAR_EXACT_MIN)

	def test_threshold_accepts_a_spelling_variance(self):
		tool = _tool()
		ratio = tool.SequenceMatcher(
			None,
			tool.normalise("ايهاب عمر بدوى الشناوى"),
			tool.normalise("ايهاب عمر بديوى الشناوى"),
		).ratio()
		self.assertGreaterEqual(ratio, tool.NEAR_EXACT_MIN)

	def test_a_margin_is_required(self):
		"""Two people with a near-identical name must not both be candidates."""
		self.assertGreater(_tool().NEAR_EXACT_MARGIN, 0)


class TestManifest(FrappeTestCase):
	"""What the host tool decided, checked on the way in."""

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls.manifest = photos.load_manifest()

	def test_manifest_accounts_for_every_photo(self):
		"""Nothing is silently dropped: matched plus rejected must equal the total.

		`unmatched` already counts the suppressed duplicates, so they must not be
		subtracted again.
		"""
		summary = self.manifest["summary"]
		total = summary["matched"] + summary["ambiguous"] + summary["unmatched"]
		self.assertEqual(total, summary["photos"], "some photos are neither matched nor reported")

	def test_duplicate_count_is_consistent(self):
		summary = self.manifest["summary"]
		self.assertEqual(
			summary["unmatched"],
			len(self.manifest["unmatched"]) + len(self.manifest["duplicate_employee"]),
		)

	def test_every_match_records_its_method_and_a_reason(self):
		for row in self.manifest["matched"]:
			self.assertIn(row["method"], photos.ACCEPTED_METHODS)
			self.assertTrue(row["source_file"])
			self.assertIn("employee_number", row)

	def test_no_employee_has_two_photos(self):
		"""Two files matching one person means at least one is wrong."""
		seen = [row["employee_number"] for row in self.manifest["matched"]]
		self.assertEqual(sorted(n for n in seen if seen.count(n) > 1), [])

	def test_every_matched_employee_exists(self):
		for row in self.manifest["matched"]:
			self.assertTrue(frappe.db.exists("Employee", row["employee_number"]), row["employee_number"])

	def test_rejected_photos_carry_a_reason(self):
		for group in ("ambiguous", "unmatched", "duplicate_employee"):
			for row in self.manifest.get(group) or []:
				self.assertTrue(row.get("reason"), f"{group}: {row} has no reason")


class TestPlan(FrappeTestCase):
	def test_plan_writes_nothing(self):
		before = frappe.db.count("Employee", {photos.IMAGE_FIELD: ("is", "set")})
		photos.build_plan()
		frappe.db.rollback()
		self.assertEqual(frappe.db.count("Employee", {photos.IMAGE_FIELD: ("is", "set")}), before)

	def test_attached_photos_exist_on_disk(self):
		"""A plan that names a file Frappe cannot read would fail mid-apply."""
		for row in photos.build_plan()["attached"]:
			self.assertTrue(pathlib.Path(row["path"]).exists(), row["path"])

	def test_attached_file_points_at_the_right_employee(self):
		for row in photos.build_plan()["attached"][:5]:
			url = frappe.db.get_value("Employee", row["employee_number"], photos.IMAGE_FIELD)
			if url:
				self.assertTrue(url.startswith("/private/files/"), url)


def _tool():
	"""The host tool, imported for its pure string functions.

	It lives in tools/ and is written to run on the host, where the photos are,
	so it is loaded by path rather than imported normally.  Skipped when absent:
	these tests must not depend on host files being present.
	"""
	path = (
		pathlib.Path(frappe.get_app_path("sharm_dreams_group")).parent / "tools" / "build_photo_manifest.py"
	)
	if not path.exists():
		raise unittest.SkipTest("tools/build_photo_manifest.py is not present here")
	spec = importlib.util.spec_from_file_location("_photo_tool", path)
	module = importlib.util.module_from_spec(spec)
	spec.loader.exec_module(module)
	return module
