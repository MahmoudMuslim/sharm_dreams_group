# Copyright (c) 2026, Sharm Dreams Group and contributors
# For license information, see license.txt

"""The employee portal, at `/employee-portal`.

The only self-service front door on this site. It began life beside
`/self-services-employees` so the two could be compared, and once everything worth
keeping over there had been moved - the server-verified biometric sign-in, the
geolocated punch, a manager's approvals, the inactivity lock - the old page was
retired rather than left running beside a newer copy of itself.

What still lives under `self_service` is the machinery this package uses rather than a
second app: `webauthn_ops` and `webauthn` (the CBOR, the key checks, the challenges)
and `geo` (the fence a punch is measured against). The session doctype keeps its
legacy name for the same reason - `Employee Self Service Session` is what the rows in
the database are called, and renaming a table is a migration, not a tidy-up.
"""