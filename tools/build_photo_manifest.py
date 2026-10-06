#!/usr/bin/env python3
"""Match the photos in `~/PIC 2025` to employees, and stage the ones that match.

The photos are named after the person, not by employee number, so matching is
done on the name.  That is inherently risky - attaching the wrong person's photo
to an employee record is worse than leaving it off - so the matcher is
deliberately layered and every decision is recorded with the method that made it.

The candidate pool is narrowed first to the employees the master marks as having
a photo on file (column 95, "6 PIC": 102 of 182).  That is an independent signal,
and it is what makes the remaining matches tractable: several of the filenames
are short Arabic forms that would otherwise collide.

Methods, best first:
  exact          the photo name equals an employee's name, ignoring case,
                 punctuation and Arabic orthography
  subset         the photo name's tokens are all inside one employee's name, and
                 exactly one employee matches
  near_exact     a near-identical name that differs only in spelling, e.g. the
                 photo "ايهاب عمر بدوى الشناوى" against the master's
                 "ايهاب عمر بديوى الشناوى".  Needs a high character similarity
                 and a clear margin over the runner-up.

Token-overlap guessing was tried and rejected: on this data it put three of the
four photos on the wrong person (a "Heba Abbas Morsy" photo on Shaaban Abbas
Morsy, and so on).  A photo on the wrong employee record is worse than no photo,
so nothing below `near_exact` is attached at all.

Anything else is left unattached and listed in the review manifest, so HR can
supply the mapping instead of the importer guessing.

The bench runs in a container that cannot see /home/mahmoud, so matched files are
copied into a staging directory inside the bench for the app side to read.

Run: python3 tools/build_photo_manifest.py [--stage]
"""

from __future__ import annotations

import argparse
import json
import pathlib
import re
import shutil
import unicodedata
from collections import Counter
from difflib import SequenceMatcher

APP = pathlib.Path(__file__).resolve().parent.parent
FIXTURE = APP / "seed_data" / "employees.json"
OUT = APP / "seed_data" / "employee_photos.json"
REVIEW = APP / "seed_data" / "photo_review.csv"
SOURCE = pathlib.Path("/home/mahmoud/PIC 2025")
STAGING = APP.parent.parent / "import_staging" / "photos"  # bench root, not inside an app
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png"}

# How alike two normalised names have to be before a photo is attached on a
# near-exact match.  Calibrated against this data: the one genuine spelling
# variance scored 0.97, and the three wrong pairings scored at most 0.62.
NEAR_EXACT_MIN = 0.85
# ...and how far clear of the next candidate it has to be, so a common name
# shared by two people cannot win on a hair.
NEAR_EXACT_MARGIN = 0.05

# Role and location decorations people put in filenames.  Stripped before
# matching, longest first so "chief technical officer -" wins over "officer -".
PREFIXES = (
	"chief commercial officer -",
	"chief technical officer -",
	"chief executive officer -",
	"chief financial officer -",
	"chief operating officer -",
	"ceo-",
	"coo-",
	"cto-",
	"ceo ",
	"coo ",
	"cto ",
)
SUFFIXES = ("- ayady", "- katamya", "- hurghada", " pic")

# Written as escapes on purpose: Arabic letters are indistinguishable from some
# Latin ones, which is exactly why they are normalised below, and spelling them
# out keeps the mapping unambiguous.
ALEF = "\u0627"
ALEF_FORMS = ("\u0623", "\u0625", "\u0622", "\u0671")  # alef with hamza, and alef wasla
ALEF_MAQSURA, YEH = "\u0649", "\u064a"  # alef maqsura, yeh
TA_MARBUTA, HEH = "\u0629", "\u0647"  # taa marbuta, heh
ARABIC_RANGE = "\u0621-\u064a"  # hamza .. yeh
ARABIC_DIACRITICS = re.compile("[\u064b-\u0670\u065f]")  # harakat
EN_DASH = "\u2013"


def normalise(text: str) -> str:
	"""Fold a name down to comparable tokens.

	Arabic needs its own handling: the sources differ on hamza forms, alef
	maqsura vs ya, taa marbuta, and harakat, so those are unified before
	comparison.  Without this no Arabic name ever matches.
	"""
	text = ARABIC_DIACRITICS.sub("", str(text or ""))
	for form in ALEF_FORMS:
		text = text.replace(form, ALEF)
	text = text.replace(ALEF_MAQSURA, YEH).replace(TA_MARBUTA, HEH)
	text = unicodedata.normalize("NFKD", text)
	text = "".join(c for c in text if not unicodedata.combining(c))
	text = text.lower()
	text = re.sub(f"[^a-z0-9{ARABIC_RANGE}]+", " ", text)
	return " ".join(text.split())


def is_arabic(text: str) -> bool:
	return bool(re.search(f"[{ARABIC_RANGE}]", text or ""))


def photo_stem(filename: str) -> str:
	"""Strip role prefixes, location suffixes and the extension."""
	stem = pathlib.Path(filename).stem
	low = stem.lower().strip()
	for prefix in PREFIXES:
		if low.startswith(prefix):
			stem = stem[len(prefix) :].lstrip(" -")
			low = stem.lower().strip()
	for suffix in SUFFIXES:
		if low.endswith(suffix):
			stem = stem[: -len(suffix)].strip()
			low = stem.lower().strip()
	# a role prefix can also sit at the end after a dash, e.g. "X - Ayady"
	return re.split(f"\\s*[-{EN_DASH}]\\s*", stem)[0].strip() or stem


def photo_files() -> list[pathlib.Path]:
	files = [
		p
		for p in SOURCE.rglob("*")
		if p.is_file() and p.suffix.lower() in IMAGE_SUFFIXES and not p.name.startswith("._")
	]
	return sorted(files)


def main() -> int:
	parser = argparse.ArgumentParser()
	parser.add_argument("--stage", action="store_true", help="copy matched files into the bench for import")
	args = parser.parse_args()

	fixture = json.loads(FIXTURE.read_text())
	employees = {r["employee_number"]: r for r in fixture["employees"]}

	# narrow to the employees the master says have a photo on file
	pool = [r for r in employees.values() if r.get("has_photo_on_file")]
	print(f"photos found        : {len(photo_files())}")
	print(f"candidates in pool  : {len(pool)} (master marks {len(pool)} as having a photo on file)")

	# name -> employee numbers
	exact_index: dict[str, set[str]] = {}
	name_tokens: dict[str, str] = {}
	for record in pool:
		full_en = f"{record['first_name']} {record['middle_name']} {record['last_name']}".strip()
		for candidate in (full_en, record["name_arabic"]):
			key = normalise(candidate)
			if key:
				exact_index.setdefault(key, set()).add(record["employee_number"])
				name_tokens[record["employee_number"]] = key

	matched: list[dict] = []
	ambiguous: list[dict] = []
	unmatched: list[dict] = []
	duplicate: list[dict] = []
	tally: Counter = Counter()

	for path in photo_files():
		stem = photo_stem(path.name)
		key = normalise(stem)
		tokens = set(key.split())

		if key and len(exact_index.get(key, set())) == 1:
			who = next(iter(exact_index[key]))
			method = "exact"
			score = len(tokens)
		elif key and len(exact_index.get(key, set())) > 1:
			ambiguous.append(
				{
					"file": path.name,
					"reason": "name matches several employees",
					"candidates": sorted(exact_index[key]),
				}
			)
			continue
		else:
			# token subset: every token of the photo name appears in the name
			subsets = {
				number
				for number, employee_key in name_tokens.items()
				if tokens and tokens <= set(employee_key.split())
			}
			if len(subsets) == 1:
				who, method, score = next(iter(subsets)), "subset", len(tokens)
			elif len(subsets) > 1:
				ambiguous.append(
					{
						"file": path.name,
						"reason": "subset matches several employees",
						"candidates": sorted(subsets),
					}
				)
				continue
			else:
				# near-exact: the same name spelled differently.  Similarity is
				# measured on the normalised string, and the winner has to be
				# clearly ahead of the runner-up.
				ratios = sorted(
					(
						(
							SequenceMatcher(None, key, name_tokens[n]).ratio(),
							n,
						)
						for n in name_tokens
					),
					reverse=True,
				)
				best, runner_up = ratios[0], ratios[1]
				similar = best[0] >= NEAR_EXACT_MIN
				margin = best[0] - runner_up[0]
				if similar and margin >= NEAR_EXACT_MARGIN:
					who, method = best[1], "near_exact"
					score = round(best[0], 3)
				else:
					unmatched.append(
						{
							"file": path.name,
							"reason": (
								f"closest {best[1]} at {best[0]:.2f}"
								f" (next {runner_up[1]} at {runner_up[0]:.2f}); "
								"below the confidence needed to attach"
							),
							"suggestion": best[1],
						}
					)
					continue

		matched.append(
			{
				"employee_number": who,
				"employee_name": f"{employees[who]['first_name']} {employees[who]['last_name']}".strip(),
				"method": method,
				"score": score,
				"source_file": path.name,
				"source_path": str(path),
			}
		)

	# One photo per employee.  Two different files matching the same person means
	# at least one is wrong, and the earlier ones win because they are matched
	# more strongly; the other is reported rather than silently dropped.
	by_employee: dict[str, dict] = {}
	for row in sorted(matched, key=lambda r: (r["method"] != "exact", r["method"] != "subset")):
		existing = by_employee.get(row["employee_number"])
		if existing:
			duplicate.append(
				{
					"file": row["source_file"],
					"reason": (
						f"already matched to {row['employee_number']} by {existing['method']} "
						f"({existing['source_file']})"
					),
					"suggestion": row["employee_number"],
				}
			)
			continue
		by_employee[row["employee_number"]] = row
	matched = list(by_employee.values())
	tally = Counter(row["method"] for row in matched)

	payload = {
		"generated_from": str(SOURCE),
		"summary": {
			"photos": len(photo_files()),
			"matched": len(matched),
			"by_method": dict(tally),
			"ambiguous": len(ambiguous),
			"duplicate_employee": len(duplicate),
			"unmatched": len(unmatched) + len(duplicate),
		},
		"matched": matched,
		"ambiguous": ambiguous,
		"duplicate_employee": duplicate,
		"unmatched": unmatched,
	}
	OUT.write_text(json.dumps(payload, indent=1, ensure_ascii=False) + "\n")

	import csv

	with REVIEW.open("w", newline="") as handle:
		writer = csv.writer(handle)
		writer.writerow(["status", "file", "employee_number", "method", "reason", "candidates"])
		for row in matched:
			writer.writerow(["matched", row["source_file"], row["employee_number"], row["method"], "", ""])
		for row in ambiguous:
			writer.writerow(["ambiguous", row["file"], "", "", row["reason"], ";".join(row["candidates"])])
		for row in unmatched:
			writer.writerow(["unmatched", row["file"], "", "", row["reason"], row.get("suggestion", "")])

	if args.stage and matched:
		if STAGING.exists():
			shutil.rmtree(STAGING)
		STAGING.mkdir(parents=True, exist_ok=True)
		for row in matched:
			src = pathlib.Path(row["source_path"])
			dest = STAGING / f"{row['employee_number']}{src.suffix.lower()}"
			shutil.copy2(src, dest)
			row["staged_path"] = str(dest)
		OUT.write_text(json.dumps(payload, indent=1, ensure_ascii=False) + "\n")
		print(f"staged {len(matched)} file(s) into {STAGING}")

	print(f"\nwrote {OUT.relative_to(APP)} and {REVIEW.relative_to(APP)}")
	print(f"  matched   : {len(matched)}  {dict(tally)}")
	print(f"  ambiguous : {len(ambiguous)}")
	print(f"  unmatched : {len(unmatched)}")
	print(f"  duplicate : {len(duplicate)} (one photo per employee)")
	return 0


if __name__ == "__main__":
	raise SystemExit(main())
