"""Employee Self Service: a wall tablet that employees sign in on with no user account.

The self-service has three parts and they are deliberately in separate modules:

* `api`, `time_off`, `approval`, `webauthn` - the guest-callable actions, all
  authorised by the token minted in `doctype.employee_self_service_session`.
* `geo`, `vacation` - the two read models those actions need (punch zones,
  leave balances), kept out so they can be tested without a session.
* `renderers` - the two website routes the browser talks to.
* `webauthn_ops` - pure crypto, no Frappe imports at all.

`www/self-service.py` and `www/self-service.html` sit at the app root because Frappe maps
`<app>/www/<name>` to the route `/<name>`.
"""