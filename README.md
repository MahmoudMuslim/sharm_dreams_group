# Sharm Dreams Group — Payroll

Egyptian payroll for the Sharm Dreams Group, built on Frappe/ERPNext/HRMS.

## What this app does

It replaces a 207-row Excel payroll workbook with a proper model of how the
group is actually structured: one **parent company with subsidiaries**, and
senior employees whose salary is **shared across several group companies**.

| Concept | DocType |
|---|---|
| An employee | HRMS `Employee` + `SDG Insured Person` |
| A salary share booked to one group company | `SDG Group Allocation` (child) |
| A staff loan | `SDG Employee Loan` |
| Insurance and benefit rates | the `SDG * Band` tables |
| Global rates and caps | `SDG Payroll Settings` |

## The three rules that matter

**1. A shared employee gets one payslip and one payment.**
Their salary is divided between the group companies that consume their time.
The leader allocation pays; the delegated allocations are cost attribution.

**2. A delegated allocation is not an employment.**
Because the statutory employment sits on the leader row, a delegated
allocation gets **no personal exemption** and is taxed at a **flat 10%** —
the group company's own withholding. The leader row keeps the full personal
exemption and the full bracket table. Both switch on the same field
(`SDG Group Allocation.group_company`).

**3. Loans come off after tax.**
The staff loan is written to `total_loan_repayment` on the Salary Slip.
HRMS's own `set_net_pay()` already subtracts that field *outside*
`total_deduction`, so loans never reduce taxable income — matching the
legacy workbook.

## Band tables

| DocType | Keyed on | Purpose |
|---|---|---|
| `SDG Medical Band` | coverage band (`75k C` / `100k B` / `150k A`) | medical premium |
| `SDG Gross Insurance Band` | `from_gross` … `to_gross` | life insurance + SI contribution base |
| `SDG Family Medical Band` | band + spouse + children | family medical premium |
| `SDG Coupon Band` | marital status + children | cost-of-living coupon |
| `SDG Tax Bracket` | `up_to` | annual income tax |
| `SDG Leave Type` | leave code | paid / unpaid |
| `SDG Employment Category` | category name | seniority band vocabulary (9 values) |
| `SDG Contract Type` | contract type name | contract vocabulary (5 values) |
| `SDG Sub Department` | sub-department name | third org level (21 values) |
| `Company.company_name_ar` | — | Arabic legal name, under `company_name` |
| `SDG Insured Person` | employee | medical cover, statutory IDs, document checklist |
| `SDG Payroll Settings` | Single | rates and caps |
| `SDG Loan Settings` | Single | loan defaults and GL accounts |

Two of these (`SDG Gross Insurance Band`, `SDG Family Medical Band`) are
**seeded from the legacy workbook** because the insurers' published schedules
were not available. They reproduce the workbook exactly; replace their contents
when the schedules arrive and nothing else needs to change.

## Data sources migrated

| Source | Supplies |
|---|---|
| `Master Colleagues Data.xlsx` | employees, org, contract, salary bands |
| `Medical.xlsx` | insured persons, coverage band, dependants, DOB |
| `Cost of living coupon.xlsx` | coupon bands and entitlements |
| `SharmDreamVacation Tracker.xlsx` | leave calendar and resignations |
| `Head Office AUG 2026.xlsx` | the validation baseline for the golden test |

## Seeding and checking the data

The legacy workbooks are parsed on the host, reviewed as JSON, and only then
written to the database. Nothing goes straight from an `.xlsx` into a live
payroll table.

```
python3 tools/extract_seed_data.py        # host: workbooks -> seed_data/seed.json
bench --site <site> execute sharm_dreams_group.seeder.seed_all_reset   # clear and reload
bench --site <site> execute sharm_dreams_group.seeder.seed_all        # idempotent reload
bench --site <site> execute sharm_dreams_group.seeder.report          # print what is loaded
bench --site <site> execute sharm_dreams_group.seed_checks.run        # verify the live tables
bench --site <site> run-tests --app sharm_dreams_group                # 23 unit tests
```

`seed_all_reset` is a separate entry point because `fm shell` mangles inline
`--kwargs`; both are safe to re-run and neither duplicates rows.

The tests deactivate the live configuration and restore it afterwards, so a
failed run cannot corrupt the real tables.

## The employee master

`Master Colleagues Data.xlsx` has 153 columns; ERPNext's `Employee` has 67. Only
about 30 map straight across, so the rest are handled deliberately:

| Where | What |
|---|---|
| Employee's own fields | `employee_number`, `company`, `date_of_birth`, `date_of_joining`, `gender`, `marital_status`, `department`, `branch`, `designation`, `contract_end_date`, `personal_email`, `company_email`, `current_address`, `cell_number` |
| `sdg_*` custom fields | Arabic name, national ID, SI number/status/date, military status, religion, governorate, dependants, offer status, employment category, contract type, sub-department |
| `Designation.sdg_designation_name_ar` | Arabic job title — held **once per designation**, not per employee |
| `Employee.sdg_starting_designation` | role held on joining, as distinct from the current one — both Link to `Designation` |
| `Employee.grade` (stock) | grade — the app adds no grade field, because ERPNext already links to `Employee Grade` |
| Computed on every save | `sdg_age`, `sdg_years_of_tenure`, `sdg_days_with_company` |
| `SDG Insured Person` | coverage band, card, statutory IDs, the 18 document-checklist flags |
| Not imported | salary and allowance columns (all 0 of 178 - the payroll workbook is the only salary source), the Manning headcount columns, and the 43 leave-history columns (left for a later phase) |

`emergency_phone_number` is parsed out of the master's single packed
next-of-kin cell rather than copied. That column looks like
`'Son - Mohamed Sabry ( 01016361630 )'` and sometimes has two numbers or no
relationship word, so it is split across `relation`, `person_to_be_contacted`
and `emergency_phone_number`.

### Name splitting

ERPNext rebuilds `employee_name` from `first_name` + `middle_name` + `last_name`
in `validate()`, so the name cannot be written directly. The rule is: first word
to `first_name`, final word to `last_name`, everything between to
`middle_name`. That reconstructs the original string exactly, which matters
because `employee_name` is what appears on the payslip.

## Reference data

Three vocabularies and the Arabic job titles are seeded from the master by
`vocabularies.py`, driven by `seed_data/master_reference.json`:

```
python3 tools/build_master_fixture.py                              # host
bench --site <site> execute sharm_dreams_group.vocabularies.seed   # create
bench --site <site> execute sharm_dreams_group.vocabularies.report # review
```

The seeder **fills fields and never empties them**: a `None` in the source means
"nothing to say", not "clear it". The master lists some titles twice differing
only in capitalisation — `Administrative Assistant` as a current position with an
Arabic name and `Administrative assistant` as a starting position without one — and
both resolve to the same row, so blanking on the second would fight the first
forever.

The site already held 197 curated `Designation` records and every one of the
master's 106 titles already exists on it — 17 of them differing only in
capitalisation.  The seeder therefore reuses the existing row and never rewrites
a designation's own name, because `Designation` names itself
(`autoname: field:designation_name`) and a rename would either be rejected or
produce a near-duplicate row that splits the link target.

Five English titles have more than one Arabic spelling in the source
(`Secretary` -> سكرتير / سكرتيرة / سكرته).  The most common is taken and the rest
are recorded in the row's `description`; `test_reference_data` pins the choice so
it cannot change silently.

The Sub Department column conflates sub-departments with place names — `Head
Office`, `Sharm El Sheikh`, `@40` and `Villa 644` are locations, and `Vacation
Club` appears as both a Department and a Sub Department.  Those rows are ticked
`looks_like_a_location` rather than silently classified, so HR can decide.

### Where the statutory fields live

Personal Details carries, in order: `sdg_national_id` and its expiry at the top
(that's the identity an Egyptian employment record is keyed on); then the personal
facts including `sdg_military_status` and `sdg_military_document`; then a
**Social Insurance** section, placed directly after the stock Health Insurance
section, holding the social insurance number, status and date. They describe the
same thing, so splitting them across two tabs left neither complete. That section
is a custom field and is collapsible, like the stock section above it.

Offer status and status date sit on the Joining tab. The **SDG Statutory tab has
been removed**: it existed to hold national ID, social insurance and military
status, all of which now live on Personal Details, and an empty tab is clutter.

Renaming a section is not just a label change. The section is a Custom Field, so
renaming it means a new fieldname, the old one has to be retired
(`OBSOLETE_EMPLOYEE_FIELDS`) or the form ends up with two section breaks, and
`PERSONAL_DETAILS_ORDER` has to list the new fieldname so the fields stay inside
it. A rename applied only to the code leaves the database showing the old label
while the setup script reports success.

`Passport Details` and `Social Insurance` are both collapsible. Passport Details
is a stock ERPNext section, so making it collapsible is a customization of a
doctype this app does not own — an ERPNext upgrade can revert it. It is
re-applied on every `create_custom_fields` run and asserted by a test, so a
revert shows up rather than passing unnoticed.

The **Joining tab is explicitly ordered** by `JOINING_TAB_ORDER` in `setup.py`:
`sdg_offer_status`, `sdg_status_date`, `job_applicant`,
`scheduled_confirmation_date`, `final_confirmation_date`, `sdg_contract_type`,
`sdg_contract_date`, `contract_end_date`, `notice_number_of_days`,
`sdg_employment_category`, `sdg_starting_designation`, `date_of_retirement` — that
is, are they on board, then confirmation milestones, then contract terms, then the
role they were hired into, then how the relationship can end.

Reordering a tab needs two mechanisms, because a tab's fields live in two places:
stock fields are listed on the `Employee` DocType and their order is set by
rewriting that list, while custom fields are merged in at read time and positioned
by their own `insert_after`. `reorder_fields_in_tab` does both, and each anchor is
written in exactly one place (`REORDER_OWNED`) so the two cannot undo each other.
It refuses a field list that does not match the tab exactly, and reports
`already in order` when there is nothing to do. An order tuple may include layout
entries (section breaks); those take part in the positioning chain but are excluded
from the data-field comparison.

The **Personal Details tab is explicitly ordered** by `PERSONAL_DETAILS_ORDER` in
the same way: identity first (national ID, expiry, Arabic name), then the personal
facts in the order they are usually asked for (marital status, dependants,
family background, religion, military status and document, age), then health
(blood group, health details), then the three document sections. The two SDG
section breaks are listed in the order tuple so the social-insurance fields cannot
jump above the section they belong to.

`Starting Designation` links to the same `Designation` table as the current one.
The master's "Starting Position Upon Joining" column holds 108 titles, 32 of which
never appear as a current position, so the Designation vocabulary was widened from
106 to 138 to give every starting title somewhere to point.

Religion, both military fields and Offer Status are Selects whose options are
exactly the values present in the master, so nothing has to be coerced into an
extra bucket:

| Field | Options | Source column |
|---|---|---|
| `sdg_religion` | Muslim / Christian | Religion (170 / 8) |
| `sdg_military_status` | Completed / Exempted / Not Required / Postponed | Military Status (70 / 76 / 24 / 1) |
| `sdg_military_document` | Missing / On File / Out and not Returned | Military Staus Document (10 / 160 / 5) |
| `sdg_offer_status` | On board / Off board | Offer Status |

Note the leavers sheet's Religion column does not line up with the master's — it
carries `Married`, `Single`, `1`, `2` and `3`, which look like marital status and
dependant counts rather than religion. The columns are shifted on that sheet, so
it cannot be imported with the same column mapping as `Master`. That is a finding
for the leavers phase, not something to guess at now. The master's document
column writes `on file` in lower case on 35 of 175 rows, so the importer
normalises case before setting it.

## Company names

`Company` is a Link field, so the master's Company column has to resolve to real
companies before anyone can be imported. The source is messy three ways: the
master column is bilingual (`"Concord For Touristic Development - كونكورد
للتنمية السياحية"`, sometimes with no space before the dash and once with no
dash at all), the payroll column holds English with typos (`"Bautex misr"`,
`"IFLEET"`), and two companies appear in neither.

`companies.py` normalises both sides — case, punctuation, `&`/`and`, and the
`Company`/`Co` token the sources sprinkle around — then requires an exact match
or an explicit override. No fuzzy matching: a wrong company on an employee is
worse than a failed import.

```
bench --site <site> execute sharm_dreams_group.companies.report
bench --site <site> execute sharm_dreams_group.companies.seed_arabic_names
```

13 of 14 companies are mapped and get `company_name_ar`. `Sharm Dreams Group` is
the group parent — no employees are booked against it in either workbook — so it
is listed in `NO_SOURCE` and left blank rather than being given a guessed name.
`seeding` throws if any *other* company is unmapped.

## The tax engine

`payroll/egypt/calculation.py` holds the arithmetic as pure functions - no
database, no document. `payroll/egypt/deductions.py` is the thin adapter that
reads the slip, calls the calculator and writes the rows back; it is registered
as the Egypt replacement for `SalarySlip.apply_regional_deductions`.

Keeping it pure is what makes the golden test possible.
`sharm_dreams_group/tests/golden_payroll.json` records, for all 207 workbook
rows, the inputs the engine needs and the values the workbook actually paid.

**The engine reproduces the workbook's income tax exactly on all 207 rows**,
to the cent, including all 37 delegated rows at the flat 10%.

### A trap worth knowing about

`hrms.allow_regional` builds its lookup key as
`f"{inspect.getmodule(fn).__name__}.{fn.__name__}"` and compares it verbatim:

    hrms.payroll.doctype.salary_slip.salary_slip.apply_regional_deductions

Note there is no class name. An earlier version of `hooks.py` had
`...salary_slip.SalarySlip.apply_regional_deductions`, which **silently never
matched** - no error, no tax, for every payslip. `setup.verify_hook_key` now
rebuilds the key the same way HRMS does and fails loudly if it drifts.

### Two figures the workbook hard-codes

23 rows of the training fund column and 2 social-insurance cells were typed over
by hand rather than left as formulas. The engine computes the correct figure and
does **not** reproduce these; they are pinned in `LEGACY_OVERRIDES` in
`test_calculation.py` so they cannot be lost, and a test asserts that list has
not grown or shrunk.

| Cell | Rows | Workbook | Formula |
|---|---|---|---|
| Training fund `BG` | 59, 63, 67, 71, 147 | 30.00 flat | `AJ * 0.25%` |
| Training fund `BG` | 78-93, 146, 158 | 0.00 | `AJ * 0.25%` |
| SI employee `AK` | 56 | 167.00 | 1,837.00 |
| SI employer `BD` | 146 | 0.00 | 3,131.25 |

Row 56's employee social insurance looks understated by 1,670 EGP and row 146's
employer share was zeroed. Both are worth a look before the next run.

## What the validations prevent

The legacy workbook had hand-typed tax offsets above 600,000 that did not agree
with the stated 25% rate, which produced tax cliffs. `SDG Tax Bracket` now
**recomputes every offset from the bands below it** and refuses to save a
bracket that does not reconcile, so that class of error cannot come back.
`SDG Gross Insurance Band` refuses holes in the table, because a gap would
silently drop an employee onto the wrong premium.

Rows are marked `<== needs review` in the report where the source data is thin
or internally inconsistent. Those are decisions for the business, not bugs:

- all three medical bands — the `75k C` sample is a single employee
- `Married + 1 Child` — the coupon register's stated total is 300 EGP adrift

## Employee self service

A wall tablet at the entrance: employees clock in, read their payslips, file leave
and approval requests, all **without a Frappe user account**. Ported from the Odoo
self-service addon, which it follows closely.

The page is `/self-services-employees`. It is deliberately **not** a desk page — it
is a website page with its own CSS and JS, linked by the page itself rather than
injected through `app_include_css` / `app_include_js`, because `self_service.css` positions
a fixed full-viewport root element that would sit on top of the desk everywhere else.

The address matches the desk label — *Self Services Employees* — rather than the word
"self-service", which describes the tablet and not the feature. The DocTypes keep their
`Employee Self Service *` names and the `self_service/` package keeps its name. Frappe derives the route
from the template filename and turns hyphens into underscores for the controller
module, so the two files are `www/self-services-employees.html` and
`www/self_services_employees.py` — the arrangement Frappe's own `www/update-password`
uses. The route is written down once, in `renderers.PAGE_ROUTE`, and `TestWiring`
checks the constant, the two filenames and the client's hardcoded API path against
each other, because none of the three is reconciled at runtime.

There is **no redirect from `/self-service`**. It 404s.

### Light and dark

The page carries both palettes, and the picker in the header offers **Light**, **Dark** and
**Match device**. The choice is a cookie, `employee_self_service_theme`, and it is resolved
in a small script in `<head>` **before the stylesheet link** — so the attribute is on
`<html>` at first paint and a tablet set to dark never flashes white. That is also why
there is deliberately no `@media (prefers-color-scheme)` block in the CSS: "Match device"
is a JavaScript decision made before the first byte of CSS is parsed, and a media query
would be a second, later, disagreeing answer. `TestThemeAndWiring` asserts the ordering,
because getting it wrong still renders correctly — it just flickers, which is the
symptom people report as "it flashes".

The palette is 90-odd tokens in two blocks, `:root` and `:root[data-k-theme="dark"]`.
Two rules govern it, and both were learned the hard way:

- **A token is a colour, never a fragment of some other declaration.** `--k-inset-hi`
  was a bare colour in light and the whole `inset 0 -1px 0 rgba(0, 0, 0, 0.3)` in dark,
  while its call site already supplied the `inset 0 1px 0` half. Spliced, that produced
  `inset 0 1px 0 inset 0 -1px 0 …`; a shadow may name `inset` only once, so the
  declaration was invalid at computed-value time and the **entire** `box-shadow` was
  dropped. Every dark button silently lost its glow and its highlight.
- **A token keeps its shape across the two palettes.** The same family also held a
  wash in one palette and a shadow in the other, which computes to `transparent` when
  spliced into a `background`.

Both are now enforced structurally rather than by eye: `TestThemeAndWiring` checks that
no token holds a declaration, that a token that is a colour in one palette is a colour in
the other, that no dark `-deep` reuses a light base verbatim, and that every
colour-carrying Bootstrap utility the client renders is remapped inside `.k-root`.

That last one matters more than it sounds. The page inherits Frappe's website bundle,
which carries Bootstrap's light palette, and two of its rules reach past
`self_service.css`: `h1…h6 { color: #171717 }` and `.text-muted { color: #7c7c7c
!important }`. The client renders `h2.fw-bold` for *Employee Sign In* and
`p.text-muted` under it, so the heading came out `#171717` on the dark card — **1.05:1**,
which is to say invisible — and the subtitle at 4.08:1. Neither value is in this app's
stylesheet, which is why a check that only reads `self_service.css` reported every colour
on the page as tokenised and was wrong.

Contrast is checked per declaration against that rule's own background, because a token
is only as good as the worst surface it lands on. It found that several `-soft` tints
were chosen to look like a *highlight* and then used as a label's background, which is a
different question — `#34d399` under a white label is 1.92:1, and the time-off tile's
label was invisible on it. Five light-palette tints and `--k-warn` moved: lightness only,
hue and saturation untouched. The gradients are flatter for it, which is the trade. A
tile nobody can read is not a highlight.

`--k-text-4` is the one token held below 4.5:1, at 3:1, and the exemption is named in
`DEEMPHASIS_TOKENS` with its reason. It paints placeholders, empty-state glyphs and the
language dropdown's caption — none of which carries information the label beside it does
not already carry — and four distinguishable steps of grey cannot all clear 4.5:1 on a
near-white surface. It is the quietest thing on screen by design, and a test holds it to
the ratio the palette's own comment claims.

### How someone signs in

National ID (`Employee.sdg_national_id`) plus Employee Number
(`Employee.employee_number`). Both are stock-or-already-there, so no new field is
involved and the number on the self-service page is the number on the contract.

The self-service then mints a **Employee Self Service Session** — one row, one opaque token, eight hours.
That token is the whole authorisation for every action afterwards; `resolve_session`
turns it back into an Employee and nothing else on the self-service page reads a user id.
Employees have no user, so two consequences run through the code:

- `request_leave` and `cancel_leave` run as the employee's own user (or
  `Administrator`), because HRMS's `validate_balance_leaves` rejects guests with a
  message an employee cannot act on.
- `Employee Self Service Approval Request.request_owner` is left empty; `employee` is who really
  raised it.

Once a device is enrolled it is **required**: `sign_in` refuses the pair and the
WebAuthn challenge becomes the only way back in.

That leaves one path worth knowing about, and it is the client's, not a back door:
`webauthnSupported()` is checked per click, so a device with no WebAuthn at all
falls through to `plainSignIn` and posts the same `sign_in` action. The server
refuses that for an enrolled account, so it can only ever be a **first** sign-in.
An old tablet with no WebAuthn support therefore enrols itself on first use and
behaves normally afterwards — which is the intended shape, and the reason the
pair check exists at all rather than the biometric being unconditional.

Two operational notes the walk-through turned up, both verified against a real
signed-in session:

- The page **signs itself out after 30 seconds of idle** (`#essRoot[data-delay]`,
  default `30000`). That is deliberate for a shared wall tablet, but it will cut
  off anyone driving the page from a script or a debugger that pauses between
  steps; raise `data-delay` rather than removing the timer.
- Punches, leave requests and approvals all key off the session token alone, so
  signing out mid-task loses nothing that was submitted and does not roll back
  anything already written.

### Why the routes are not `/api/method`

Both self-service routes are page renderers. On a bench running the `press` app, its auth
hook rejects every `/api/` path for a non-system user — so a self-service front-end that
used `/api/method` would work on one bench and 404 on another. Nothing is opened up
by moving off `/api/`: every action is still authorised by the session token. It
just stops the answer depending on which apps are installed.

### Geofencing starts **off**, and that is deliberate

A punch zone is a centre point and a radius. `Company` has no latitude or
longitude field, and no coordinate for any of the group companies exists in the
master workbook or the seed data — so seeding a zone would mean inventing a
number, and enforcement on with a wrong centre refuses every punch at the real
office.

So install leaves `geo_punch_enforce` at 0 and prints what is left to do. Punches
work and every one records its coordinates. HR adds a `Employee Self Service Location` per site and
turns the flag on. Note the asymmetry: `self_service.geo.is_enforced` fails **closed** to
enforced when the setting cannot be read at all, so an unreachable database refuses
punches, but an explicitly-off setting is an instruction and is obeyed.

`Employee Checkin.sdg_self_service_geo_status` and `sdg_self_service_geo_message` carry the
verdict. Read-only — the self-service page writes them, nobody edits them.

### The vacation balances

The balances screen shows entitlement minus usage, for six buckets. Only the
entitlement half is stored — six `sdg_vacation_*_days` Float fields on Employee.
The spent half is **derived** from the employee's Leave Applications, so the two
cannot drift; a stored "used" figure eventually would.

Each leave is weighted by its Leave Type's `sdg_vacation_day_multiplier`, seeded to
2 for `WD` (working a weekly rest day burns two days of allowance) and 1 for
everything else. Bucket codes come from the Sharm leave vocabulary in
`seed.json`; `PH`, `REM` and `CF` are not leave types HR has created yet, so they
count nothing today and start counting the day one is given that code.

Note this coexists with `Leave Allocation`, which is where HRMS keeps entitlement.
Nothing else in this app reads these six fields — they exist for the self-service page.

### Where the self-service tables live

On the workspace as a **Self Services Employees** card, and in the desk sidebar
under **Time & Attendance → Self Services Employees**. The label is for what the
employee does; the DocTypes keep their `Employee Self Service *` names, so this is a display string
and not a schema rename. The workspace card exists because `workspace.py` refuses to
pass when a DocType this app owns is not reachable from the desk.

Two of the seven are **System Manager only**, deliberately:

- `Employee Self Service Session` — `access_token` is stored in the clear, because it is the whole
  authorisation and a hashed column could not be looked up by value. Anyone who can
  read it can act as that employee on the self-service page.
- `Employee Self Service Preauth` — a live row is a biometric sign-in in progress, and answering its
  challenge enrols a device for that employee.

They are linked anyway: a System Manager debugging "this employee cannot sign in"
needs to see them. The link 403s for HR rather than 404ing, which is the honest
outcome — they exist, they are just not yours. The other five (`Employee Self Service Settings`,
`Employee Self Service Location`, `Employee Self Service Biometric Credential`, `Employee Self Service Approval Category`,
`Employee Self Service Approval Request`) are HR's to administer.

### Files

| Path | What |
| --- | --- |
| `self_service/api.py` | sign-in, profile, punch, payslips, leave |
| `self_service/time_off.py` | balances, cancelling a request |
| `self_service/approval.py` | request types, submission, tracking |
| `self_service/webauthn.py` + `webauthn_ops.py` | biometric sign-in; the second has no Frappe imports at all |
| `self_service/geo.py` | the punch zone check |
| `self_service/vacation.py` | the balance arithmetic |
| `self_service/renderers.py` | `/self-services-employees-api` and `/self-services-employees/setlang` |
| `www/self-services-employees.html` + `www/self_services_employees.py` | the page |
| `self_service/test_self_service.py` | 80 tests |

`renderers.ACTIONS` is the front door: an action missing from that map is
unreachable over HTTP however correct the client is. `TestWiring` asserts the map
and `self_service.js` agree from both ends — it is how a missing `approvals` entry was
caught during the port.

### Tests

    bench --site <site> run-tests --app sharm_dreams_group \
        --module sharm_dreams_group.self_service.test_self_service

Safe to run against the live site. Two self-service actions commit on purpose, and
`FrappeTestCase` rolls back per *class* rather than per test while opening each
class with a commit — so the base class stubs `commit` for the class, purges its own
namespace at both ends, and commits the final purge. Everything it creates is named
with the `ESS-TEST-` prefix, which is what lets cleanup be both safe (it cannot
touch a real employee) and self-healing (it clears its own residue from an earlier
run).

## License

AGPL-3.0
# sharm_dreams_group
# sharm_dreams_group
