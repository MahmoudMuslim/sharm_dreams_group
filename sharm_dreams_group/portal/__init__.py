# Copyright (c) 2026, Sharm Dreams Group and contributors
# For license information, see license.txt

"""The employee portal, at `/employee-portal`.

The single self-service surface for employees. The old `/self-services-employees`
page and its client were removed once their remaining features - biometric sign-in,
remembered identities, geofenced punching, approvals and the idle timeout - were
carried over here. `Employee Self Service Session` is still shared, because a token
does not care which page minted it and forking that doctype would mean two login
histories for one tablet.
"""