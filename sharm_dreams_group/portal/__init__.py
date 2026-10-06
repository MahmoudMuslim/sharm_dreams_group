# Copyright (c) 2026, Sharm Dreams Group and contributors
# For license information, see license.txt

"""The employee portal, at `/employee-portal`.

A second front door to the same employees, built alongside `/self-services-employees`
so the two can be compared before the old page is retired. Nothing here changes or
depends on the old page: its own route, its own renderer, its own template, and its
own JavaScript. The one thing shared is `Employee Self Service Session`, because a
token does not care which page minted it and forking that doctype would mean two
login histories for one tablet.
"""