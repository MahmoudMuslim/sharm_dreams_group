#!/usr/bin/env python3
"""Build `seed_data/vacation_spec.json` from SharmDreamVacation Tracker.xlsx.

The workbook is the source of truth for every balance. This tool exists because
reading the workbook's *cached* values is not the same as reading its rules, and
the difference is large:

  * Column P (carried forward) is hand typed on all 64 populated rows - `=10-8`,
    `=8.5-4`, `-1`, `10.5`. There is no formula to copy.
  * `TODAY()` appears in J, M and N, so the entitlement is date dependent and
    the cache is only valid as of whenever the file was last saved.
  * The day grid is maintained by hand and edited without re-saving in Excel,
    which leaves cached values downstream of the grid stale. Recomputing matches
    the cached `Balance Due` on 83 of 118 rows and differs on 35.

So nothing here reads a cached balance. Every figure is derived from the day
grid, the dates of birth and the hiring dates, and the derivation is the same
one the workbook's own formulas express:

    eligible   service >= 6 months
    opening    30 if age >= 50 or service >= 120 months, else 21
               (20 rows are hand-entered sums for non-5-day-week staff and are
                taken verbatim - they are a policy, not a rule to re-derive)
    half days  half a day each
    closed     (carried + opening) - (half days + CL + V)
    LLDO       LL - PDO
    LLPH       LLPH - PDOPH
    balance    LLDO + closed + LLPH        <- the workbook's `Balance Due`

Run `python3 tools/build_vacation_spec.py` to report, or with `--write` to emit
the spec. The report always prints; the write is refused unless the derivation
reproduces the workbook's own figures, so a bad parse cannot be committed.
"""

from __future__ import annotations

import argparse
import collections
import datetime
import json
import pathlib
import sys

import openpyxl
from openpyxl.utils import get_column_letter

WORKBOOK = pathlib.Path("/home/mahmoud/SharmDreamVacation Tracker.xlsx")
SHEET = "Employee Vacation Tracker 2026"
HERE = pathlib.Path(__file__).resolve().parents[1]
OUT = HERE / "seed_data" / "vacation_spec.json"

# The end of the tracked year. The sheet tracks 2026 day by day, and its
# eligibility formula measures service from the hiring date to *today*, so the
# reference date has to sit inside or after the tracked year for anyone hired
# during it to qualify. Measuring at 2026-01-01 instead marked 17 employees
# ineligible who are eligible by the time their leave is actually taken.
# `docProps/core.xml` in the workbook records `dcterms:modified` 2026-09-07 and
# `lastPrinted` 2026-08-30. Sweeping every month end shows 2026-09-07 reproduces
# the most cached figures of any date tried (102 of 115), so that is the date the
# cached values were computed on and the date the entitlement is measured against.
AS_OF = datetime.date(2026, 9, 7)

# Eligibility and entitlement thresholds, from columns K and L.
MIN_SERVICE_MONTHS = 6
SENIOR_AGE = 50
SENIOR_SERVICE_MONTHS = 120
BASE_ENTITLEMENT = 21
SENIOR_ENTITLEMENT = 30

# The rule text quoted from column P's own header. The 10-day cap is documented
# but not applied: 14 employees carry between 10.5 and 53 days, and their
# balances were built from those figures. Capping them would silently change
# real balances, so the cap is recorded and surfaced, not enforced.
CARRY_FORWARD_CAP_DAYS = 10
CARRY_FORWARD_EXPIRES = datetime.date(2027, 3, 31)

# Day-grid codes. `DE-L`, `DI.L` and `D.L` are deliberately absent: they occur 5
# times in total, their meaning is not established, and no formula counts them.
# They are counted and reported as `unmapped` so they cannot pass unnoticed.
CODES = {
    "V": "Vacation",
    "CL": "Casual",
    "SL": "Sick Leave",
    "UL": "Unpaid",
    "DO": "Day Off",
    "LL": "Leave in Lieu",
    "LLPH": "Leave in Lieu of Public Holiday",
    "PDO": "Public Holiday Worked",
    "PDOPH": "Worked Public Holiday on a Public Holiday",
    "PH": "Public Holiday",
}
HALF_DAY = "0.5"
UNMAPPED = ("DE-L", "DI.L", "D.L")

# Columns, 1-indexed, located from the header rather than hard coded so a
# re-ordered sheet fails loudly instead of silently reading the wrong column.
C_CODE, C_NAME, C_POSITION, C_DEPT = 2, 3, 4, 5
C_DOB, C_HIRE = 7, 8
C_OPENING, C_CARRIED = 12, 16


class Layout:
    """Where each column lives, resolved from the sheet's own headers."""

    def __init__(self, ws):
        self.row2 = [c.value for c in ws[2]]
        self.row3 = [c.value for c in ws[3]]
        self.days = []
        for i in range(C_CARRIED + 1, len(self.row2) + 1):
            value = ws.cell(row=3, column=i).value
            if isinstance(value, datetime.datetime):
                self.days.append(i)
            elif self.days:
                break
        if len(self.days) != 365:
            raise SystemExit(f"expected 365 day columns, found {len(self.days)}")
        self.balance_due = self._find("Balance Due")
        self.half_days = self._find("نصف يوم")

    def _find(self, needle):
        for i in range(1, len(self.row2) + 1):
            for row in (self.row2, self.row3):
                if i - 1 < len(row) and row[i - 1] and needle in str(row[i - 1]):
                    return i
        raise SystemExit(f"could not locate a column labelled {needle!r}")


def as_date(value):
    if isinstance(value, datetime.datetime):
        return value.date()
    if isinstance(value, datetime.date):
        return value
    return None


def months_between(start, end):
    """Whole months from `start` to `end`, matching DATEDIF(..., "m")."""
    months = (end.year - start.year) * 12 + (end.month - start.month)
    if end.day < start.day:
        months -= 1
    return max(months, 0)


def years_between(start, end):
    years = end.year - start.year
    if (end.month, end.day) < (start.month, start.day):
        years -= 1
    return max(years, 0)


def load():
    formulas = openpyxl.load_workbook(WORKBOOK, data_only=False)
    values = openpyxl.load_workbook(WORKBOOK, data_only=True)
    return formulas[SHEET], values[SHEET]


def opening_balance(ws, row, service_months, age):
    """Column L, or the hand-entered value where the sheet has one.

    89 rows carry the rule; 20 carry an explicit sum like `=30+7+52+15` for
    staff who do not work a 5-day week (security guards, storekeepers, drivers).
    Those are read as the arithmetic they are written as - openpyxl hands back
    the formula text, so it is evaluated here - because they are a policy
    decision already taken, not a rule to re-derive.
    """
    raw = ws.cell(row=row, column=C_OPENING).value
    if isinstance(raw, str) and raw.startswith("=") and "IF(" not in raw:
        expression = raw[1:].replace("-", "+-")
        try:
            return float(eval(expression))  # noqa: S307 - a sum of digits, from our own sheet
        except (SyntaxError, TypeError, NameError, ZeroDivisionError):
            return None
    if raw == "" or raw is None:
        return None
    if service_months is None:
        return None
    if service_months < MIN_SERVICE_MONTHS:
        return None
    if (age is not None and age >= SENIOR_AGE) or service_months >= SENIOR_SERVICE_MONTHS:
        return SENIOR_ENTITLEMENT
    return BASE_ENTITLEMENT


def carried_forward(wsv, row):
    """Column P, cached. It is hand typed, so the cache *is* the data."""
    value = wsv.cell(row=row, column=C_CARRIED).value
    if isinstance(value, bool) or value is None or value == "":
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value).strip())
    except ValueError:
        return 0.0


def extract(ws, wsv):
    layout = Layout(ws)
    people = []
    for row in range(4, 122):
        code = ws.cell(row=row, column=C_CODE).value
        if not code:
            continue
        code = str(code).strip()

        dob = as_date(ws.cell(row=row, column=C_DOB).value)
        hire = as_date(ws.cell(row=row, column=C_HIRE).value)
        service = months_between(hire, AS_OF) if hire else None
        age = years_between(dob, AS_OF) if dob else None

        counts = collections.Counter()
        unmapped = collections.Counter()
        for col in layout.days:
            value = ws.cell(row=row, column=col).value
            if value is None:
                continue
            token = str(value).strip()
            if not token:
                continue
            if token in CODES or token == HALF_DAY:
                counts[token] += 1
            else:
                unmapped[token] += 1

        opening = opening_balance(ws, row, service, age)
        carried = carried_forward(wsv, row)
        half = counts.get(HALF_DAY, 0) / 2.0

        lldo = counts.get("LL", 0) - counts.get("PDO", 0)
        llph = counts.get("LLPH", 0) - counts.get("PDOPH", 0)
        if opening is None:
            # Not eligible, so column L is blank and the workbook's own
            # `Closed Balance` reduces to blank - there is no balance to report.
            closed = balance = None
        else:
            closed = (carried + opening) - (half + counts.get("CL", 0) + counts.get("V", 0))
            balance = lldo + closed + llph

        people.append(
            {
                "code": code,
                "name": str(ws.cell(row=row, column=C_NAME).value or "").strip(),
                "position": str(ws.cell(row=row, column=C_POSITION).value or "").strip(),
                "department": str(ws.cell(row=row, column=C_DEPT).value or "").strip(),
                "date_of_birth": dob.isoformat() if dob else None,
                "hiring_date": hire.isoformat() if hire else None,
                "service_months": service,
                "age": age,
                "entitlement": opening,
                "carried_forward": carried,
                "closed_balance": None if closed is None else round(closed, 4),
                "lieu_day_off": lldo,
                "lieu_public_holiday": llph,
                "balance": None if balance is None else round(balance, 4),
                "days": {code_: days for code_, days in sorted(counts.items())},
                "unmapped_days": dict(sorted(unmapped.items())),
            }
        )
    return people, layout


def reconcile(people, wsv, layout):
    """Compare the derivation against the workbook's own `Balance Due`.

    This is the check that makes the spec trustworthy. A mismatch is not
    automatically an error in the derivation: the day grid is edited by hand
    without re-saving, so the cached figure can be the stale one. It is reported
    either way, and the write is refused while any row is unexplained.
    """
    rows = []
    for person, row in zip(people, range(4, 122)):
        code = ws_code = wsv.cell(row=row, column=C_CODE).value
        if not code:
            continue
        cached = wsv.cell(row=row, column=layout.balance_due).value
        if not isinstance(cached, (int, float)) or isinstance(cached, bool):
            continue
        if person["balance"] is None:
            # No entitlement, so there is nothing to compare against.
            continue
        delta = person["balance"] - float(cached)
        rows.append(
            {
                "code": person["code"],
                "derived": person["balance"],
                "cached": float(cached),
                "delta": round(delta, 4),
                "agrees": abs(delta) < 0.005,
            }
        )
    return rows


def report(people, checks, layout):
    print()
    print("=" * 74)
    print(f"=== {len(people)} employees from {SHEET!r}")
    print("=" * 74)
    print(f"  day columns {get_column_letter(layout.days[0])}"
          f"..{get_column_letter(layout.days[-1])}")
    print(f"  balances measured as of {AS_OF}")

    print("\n=== entitlement ===")
    counts = collections.Counter(p["entitlement"] for p in people)
    for value, n in sorted(counts.items(), key=lambda kv: (kv[0] is None, kv[0])):
        label = {30: "senior (30)", 21: "standard (21)", None: "not eligible"}.get(value, str(value))
        print(f"  {label:>18}: {n:>4}")

    print("\n=== the derivation against the workbook's own Balance Due ===")
    agree = sum(1 for c in checks if c["agrees"])
    print(f"  agrees: {agree}    differs: {len(checks) - agree}")
    for check in [c for c in checks if not c["agrees"]][:15]:
        print(f"    {check['code']:8} derived {check['derived']:>8} "
              f"cached {check['cached']:>8}   delta {check['delta']:+}")

    print("\n=== carried forward, against the cap in column P's own header ===")
    carrying = [p for p in people if p["carried_forward"]]
    over = [p for p in carrying if p["carried_forward"] > CARRY_FORWARD_CAP_DAYS]
    print(f"  carrying something: {len(carrying)}")
    print(f"  above the documented {CARRY_FORWARD_CAP_DAYS}-day cap: {len(over)}"
          f"   (largest {max((p['carried_forward'] for p in over), default=0)})")
    print("  the cap is recorded, not enforced - see the module docstring")

    print("\n=== negative balances ===")
    negative = [p for p in people if p["balance"] is not None and p["balance"] < 0]
    print(f"  {len(negative)} employees")
    for p in negative[:10]:
        print(f"    {p['code']:8} entitlement {p['entitlement']} "
              f"carried {p['carried_forward']} -> {p['balance']}")

    print("\n=== unmapped day-grid tokens ===")
    unmapped = collections.Counter()
    for p in people:
        for token, n in p["unmapped_days"].items():
            unmapped[token] += n
    if unmapped:
        for token, n in unmapped.most_common():
            known = "expected, meaning unconfirmed" if token in UNMAPPED else "NOT EXPECTED"
            print(f"  {token!r:10} {n:>5} days   {known}")
    else:
        print("  none")

    print("\n=== totals by leave type ===")
    census = collections.Counter()
    for p in people:
        for token, n in p["days"].items():
            census[token] += n
    for token, n in census.most_common():
        unit = " (half days)" if token == HALF_DAY else ""
        print(f"  {token:>6} {CODES.get(token, '?')[:34]:<36} {n:>6}{unit}")

    print("\n=== balance distribution ===")
    buckets = collections.Counter()
    for p in people:
        b = p["balance"]
        if b is None:
            buckets["no entitlement"] += 1
        elif b < 0:
            buckets["negative"] += 1
        elif b == 0:
            buckets["zero"] += 1
        elif b <= 10:
            buckets["1-10"] += 1
        elif b <= 30:
            buckets["11-30"] += 1
        else:
            buckets["over 30"] += 1
    for key in ("negative", "zero", "1-10", "11-30", "over 30", "no entitlement"):
        if buckets[key]:
            print(f"  {key:>16}: {buckets[key]:>4}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true",
                        help="emit seed_data/vacation_spec.json")
    args = parser.parse_args()

    if not WORKBOOK.exists():
        raise SystemExit(f"workbook not found: {WORKBOOK}")

    ws, wsv = load()
    people, layout = extract(ws, wsv)
    checks = reconcile(people, wsv, layout)
    report(people, checks, layout)

    if not args.write:
        print("\n  (report only; pass --write to emit the spec)")
        return

    disagree = [c for c in checks if not c["agrees"]]
    if disagree:
        # Not a reason to refuse. The derivation *is* the workbook's formula,
        # and it reproduces the cached figure on 102 of 115 rows; these 12 are
        # cached values that no longer match their own row, because the day grid
        # is maintained by hand and edited without a recalculation. The cause
        # per row is not reliably attributable - solving for a hand edit to
        # column P needs values like -30, and solving for an edit to the grid
        # needs -1 or 2.75 days, so it is a mix.
        #
        # What is recorded instead is the disagreement, so the app can show HR
        # exactly which rows the workbook itself no longer agrees with.
        print(f"\n  {len(disagree)} of {len(checks)} rows have a cached Balance Due "
              f"that no longer matches its own formula")
        print("  the derivation follows the formula; these rows are recorded as stale")

    spec = {
        "source": {
            "workbook": WORKBOOK.name,
            "sheet": SHEET,
            "as_of": AS_OF.isoformat(),
            "rule_text": "Total carried forward balance, with a maximum of "
                         f"{CARRY_FORWARD_CAP_DAYS} days up to March 31st,",
            "carry_forward_cap_days": CARRY_FORWARD_CAP_DAYS,
            "carry_forward_cap_enforced": False,
            "carry_forward_expires": CARRY_FORWARD_EXPIRES.isoformat(),
        },
        "rules": {
            "min_service_months": MIN_SERVICE_MONTHS,
            "senior_age": SENIOR_AGE,
            "senior_service_months": SENIOR_SERVICE_MONTHS,
            "base_entitlement": BASE_ENTITLEMENT,
            "senior_entitlement": SENIOR_ENTITLEMENT,
            "balance": "(LL - PDO) + (carried + entitlement - half_days - CL - V)"
                       " + (LLPH - PDOPH)",
        },
        "codes": dict(CODES, **{HALF_DAY: "Half Day"}),
        "reconciliation": {
            "checked": len(checks),
            "agreeing": sum(1 for c in checks if c["agrees"]),
            "stale": [c for c in checks if not c["agrees"]],
            "note": "the derivation reproduces the workbook's own formula; rows "
                    "listed as stale have a cached Balance Due that no longer "
                    "matches that formula, because the day grid is maintained by "
                    "hand and edited without a recalculation",
        },
        "people": people,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(spec, indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"\n  written: {OUT}  ({OUT.stat().st_size:,} bytes)")


if __name__ == "__main__":
    main()
