"""Shared machinery behind the employee portal.

What lives here is used by `sharm_dreams_group.portal`, not by a page of its own:

* `geo` - the punch fence, read by `portal.attendance`.
* `webauthn`, `webauthn_ops` - the WebAuthn server, read by `portal.biometrics`.
  `webauthn_ops` is pure crypto and imports no Frappe at all, which is what lets it
  be tested without a site.

`api`, `approval`, `time_off`, `vacation`, `vacation_spec` are the older
implementation of the same employee-facing actions. The page that reached them over
HTTP - `/self-services-employees`, with its `renderers` and its own JS and CSS - is
retired; those files are parked in `backups/retired-self-services-employees-2026-10-26`
beside the bench rather than deleted. The modules stay because `vacation_spec` is the
generated table `setup.py` describes, because `geo` and `vacation` are read by `api`,
and because `self_service.test_self_service` covers all of it. Nothing on the network
can reach them: with `renderers` gone there is no route that maps an action onto
`api`.

"""
