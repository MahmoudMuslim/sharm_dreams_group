/* Sharm Dreams Group employee self-service client.
 *
 * A faithful port of the Odoo kiosk front-end (static/src/kiosk/hr_kiosk_app.js)
 * onto Frappe's /self-services-employees-api JSON endpoints. Odoo's OWL component
 * is replaced by a small screen state machine; every screen, label and colour
 * comes from the Odoo templates and stylesheet it was ported from.
 *
 * Screens: login -> greet (post-punch) -> dashboard -> {payslips, approvals,
 * my_requests, time_off, time_off_requests}.
 *
 * Every action it calls is listed in `self_service/renderers.py::ACTIONS`; a name that
 * is not in that map is unreachable however correct it is here.
 */
(function () {
	"use strict";

	// The action endpoint. Must equal `renderers.API_ROUTE`; `TestWiring` reads
	// this line back and compares, because a client pointing at a route the
	// renderer does not serve gets "Unknown self-service action" on the first screen and
	// nothing in any log says the URL is the problem.
	var API = "/self-services-employees-api";
	// localStorage is per-origin, not per-app, so the app name is in the key to
	// keep two self-services on the same host from overwriting each other's identities.
	//
	// The `self-service` in the key is the app's own package name, not the URL, so it is
	// left alone: renaming it would silently empty every tablet's recent-sign-in
	// list, and the key's only job is to be unique per app.
	var RECENT_KEY = "sharm_dreams_group.self_service.recent_signins";
	var RECENT_MAX = 5;
	var CONFETTI = 10;

	var strings = window.SELF_SERVICE_I18N || {};
	var host = document.getElementById("screenHost");
	var root = document.getElementById("essRoot");

	var state = {
		screen: "login",
		lang: (root && root.dataset.lang) || "en",
		loading: false,
		error: "",
		geoWaiting: false,
		biometricWaiting: false,
		token: null,
		employee: null,
		greet: { action: "", time: "" },
		recent: readRecent(),
		listGroup: false,
		offline: false,
		inactivityMs: parseInt((root && root.dataset.delay) || "30000", 10)
	};

	/* ------------------------------------------------------------ helpers */

	function t(key, fallback) {
		var v = strings[key];
		return v === undefined || v === null || v === "" ? fallback || key : v;
	}

	function esc(value) {
		return String(value === undefined || value === null ? "" : value)
			.replace(/&/g, "&amp;")
			.replace(/</g, "&lt;")
			.replace(/>/g, "&gt;")
			.replace(/"/g, "&quot;")
			.replace(/'/g, "&#39;");
	}

	function intlLocale() {
		return state.lang === "ar" ? "ar-EG" : state.lang === "fr" ? "fr-FR" : "en-GB";
	}

	/* Odoo's formatHours: decimal hours -> "7h 05m" / "7h" */
	function formatHours(value) {
		var minutes = Math.max(0, Math.round((Number(value) || 0) * 60));
		var h = Math.floor(minutes / 60);
		var m = minutes % 60;
		return m ? h + "h " + String(m).padStart(2, "0") + "m" : h + "h";
	}

	function parseDate(value) {
		if (!value) return null;
		var d = new Date(String(value).replace(" ", "T"));
		return isNaN(d.getTime()) ? null : d;
	}

	function formatTime(value) {
		var d = parseDate(value);
		return d
			? d.toLocaleTimeString(intlLocale(), { hour: "2-digit", minute: "2-digit" })
			: value
				? String(value)
				: "—";
	}

	function formatDate(value) {
		var d = parseDate(value);
		return d
			? d.toLocaleDateString(intlLocale(), { day: "2-digit", month: "short", year: "numeric" })
			: value || "";
	}

	/* Odoo's greeting(): <12 Good Morning, <18 Good Afternoon, else Good Evening */
	function greeting() {
		var hour = new Date().getHours();
		if (hour < 12) return t("good_morning", "Good Morning");
		if (hour < 18) return t("good_afternoon", "Good Afternoon");
		return t("good_evening", "Good Evening");
	}

	function isCheckedIn() {
		return !!state.employee && state.employee.attendance_state === "checked_in";
	}

	function csrf() {
		return (window.CSRF_TOKEN && window.CSRF_TOKEN.length === 56 ? window.CSRF_TOKEN : null)
			|| (document.cookie.match(/csrf_token=([\w-]+)/) || [])[1]
			|| "";
	}

	/* The self-service holds an opaque session token, not a Frappe login, so errors are
	 * surfaced by hand. Reporting the status and content type is what makes the
	 * expired-CSRF HTML response diagnosable instead of an opaque JSON parse
	 * error in the browser console. */
	function call(payload) {
		return fetch(API, {
			method: "POST",
			credentials: "same-origin",
			headers: { "Content-Type": "application/json", "X-Frappe-CSRF-Token": csrf() },
			body: JSON.stringify(payload)
		})
			.then(function (res) {
				setOffline(false);
				return res.text().then(function (text) {
					try {
						var data = JSON.parse(text);
						if (data.exc_type || data.exc) {
							var msg = data._server_messages;
							throw new Error(msg ? String(msg) : data.exc || "Request failed.");
						}
						if (data.message && data.message.error) throw new Error(data.message.error);
						return data.message;
					} catch (e) {
						if (e instanceof SyntaxError) {
							throw new Error(
								t("server_error_detail", "Server error") + " (HTTP " + res.status + ", " +
								(res.headers.get("content-type") || "?") + "): " + text.slice(0, 120)
							);
						}
						throw e;
					}
				});
			})
			.catch(function (e) {
				// A dead self-service session must not leave the user on a broken screen.
				if (/expired or invalid|Token Expired|Invalid CSRF/i.test(e.message || "")) {
					resetToLogin();
				}
				// fetch only rejects on a transport failure, i.e. the site is
				// unreachable. That deserves its own state, not a raw exception.
				// Match on the name, not `instanceof`: a TypeError raised inside a
				// different realm (iframe, worker) is not an instance of ours.
				if (e && e.name === "TypeError") {
					setOffline(true);
					throw new Error(t("offline", "Cannot reach the server. Check the network and try again."));
				}
				throw e;
			});
	}

	/* A wall self-service has no devtools, so an unreachable server gets a persistent,
	 * actionable banner with a retry rather than a transient message. */
	function setOffline(down) {
		if (state.offline === down) return;
		state.offline = down;
		var box = document.getElementById("essOffline");
		if (!down) {
			if (box) box.remove();
			return;
		}
		if (!box) {
			box = document.createElement("div");
			box.id = "essOffline";
			box.className = "k-offline";
			document.body.appendChild(box);
		}
		box.innerHTML =
			'<div class="k-alert k-alert-warn mb-0" role="alert">' +
			'<i class="fa fa-wifi"></i><span><strong>' + esc(t("offline", "Cannot reach the server")) +
			"</strong><br><span style=\"font-size:.8em\">" +
			esc(t("offline_hint", "Check the network connection, then retry.")) + "</span></span>" +
			'<button type="button" class="k-pill-btn" id="offlineRetry">' +
			'<i class="fa fa-refresh"></i> ' + esc(t("retry", "Retry")) + "</button></div>";
		bind(document.getElementById("offlineRetry"), "click", function () {
			setOffline(false);
			call({ action: "my_profile", token: state.token })
				.then(function () {
					announce(t("back_online", "Back online"));
				})
				.catch(function () {
					setOffline(true);
				});
		});
		announce(t("offline", "Cannot reach the server"));
	}

	/* Assistive tech is told what changed, because the self-service page swaps whole screens
	 * and otherwise nothing would move or be announced. */
	function announce(message) {
		var live = document.getElementById("essLive");
		if (!live) return;
		live.textContent = "";
		window.setTimeout(function () {
			live.textContent = message;
		}, 30);
	}

	/* Move focus to the screen's primary control so the self-service page is keyboard-usable
	 * and the next action is obvious. Only explicitly marked elements qualify:
	 * auto-focusing the punch button would let a stray Enter key record a real
	 * attendance entry. */
	function focusPrimary() {
		var el = document.querySelector("#screenHost [data-autofocus]");
		if (!el) return;
		try {
			el.focus({ preventScroll: true });
		} catch (e) {
			el.focus();
		}
	}

	/* ------------------------------------------------- remembered identities */

	function readRecent() {
		try {
			var raw = JSON.parse(localStorage.getItem(RECENT_KEY) || "[]");
			return Array.isArray(raw) ? raw.slice(0, RECENT_MAX) : [];
		} catch (e) {
			return [];
		}
	}

	function writeRecent() {
		try {
			localStorage.setItem(RECENT_KEY, JSON.stringify(state.recent.slice(0, RECENT_MAX)));
		} catch (e) {
			/* private mode: remembered identities are a nicety, not a requirement */
		}
	}

	function rememberIdentity(nationalId, employeeCode) {
		if (!nationalId) return;
		state.recent = state.recent.filter(function (x) {
			return x.nationalId !== nationalId;
		});
		state.recent.unshift({ nationalId: nationalId, employeeCode: employeeCode || "" });
		state.recent = state.recent.slice(0, RECENT_MAX);
		writeRecent();
	}

	function forgetIdentity(nationalId) {
		state.recent = state.recent.filter(function (x) {
			return x.nationalId !== nationalId;
		});
		writeRecent();
	}

	/* ------------------------------------------------------------- screens */

	function showScreen(screen) {
		state.screen = screen;
		state.error = "";
		armInactivity();
		render();
		announce(screenTitle(screen));
		focusPrimary();
		// The approvals badge is fetched on entry to the dashboard, never from
		// render(), so the count lands in the same paint instead of a second
		// re-render (and render() can't re-trigger itself).
		if (screen === "dashboard" && state.token) refreshApprovalCount();
	}

	function refreshApprovalCount() {
		return call({ action: "approvals", token: state.token })
			.then(function (rows) {
				state.approvalCount = Array.isArray(rows) ? rows.length : 0;
				if (state.screen === "dashboard") render();
			})
			.catch(function () {});
	}

	function render() {
		switch (state.screen) {
			case "dashboard":
				return renderDashboard();
			case "greet":
				return renderGreet();
			case "payslips":
				return renderPanel("payslips");
			case "approvals":
				return renderPanel("approvals");
			case "my_requests":
				return renderPanel("my_requests");
			case "time_off":
				return renderPanel("time_off");
			default:
				return renderLogin();
		}
	}

	/* -------------------------------------------------------------- login */

	function renderLogin() {
		var recent = state.recent
			.map(function (entry) {
				return (
					'<button type="button" class="k-recent" data-recent-id="' + esc(entry.nationalId) + '">' +
					'<span class="d-flex align-items-center" style="min-width:0">' +
					'<span class="k-recent-icon me-2"><i class="fa fa-history"></i></span>' +
					'<span class="text-truncate">' + esc(entry.nationalId) +
					(entry.employeeCode ? '<span class="text-muted small ms-2">' + esc(entry.employeeCode) + "</span>" : "") +
					"</span></span>" +
					'<span class="k-recent-x" data-forget-id="' + esc(entry.nationalId) + '" role="button" title="' +
					esc(t("remove", "Remove")) + '"><i class="fa fa-times"></i></span>' +
					"</button>"
				);
			})
			.join("");

		host.innerHTML =
			'<div class="k-screen-center k-fade">' +
			'<div class="k-stack">' +
			'<div class="k-card"><span class="k-card-accent"></span>' +
			'<div class="k-card-body">' +
			'<div class="text-center mb-4">' +
			'<div class="k-hero-ring mb-3">' +
			'<span class="k-pulse"></span>' +
			'<span class="k-brand-mark"><i class="fa fa-id-card"></i></span>' +
			"</div>" +
			'<h2 class="fw-bold mb-1">' + esc(t("employee_sign_in", "Employee Sign In")) +
			' <i class="fa fa-lock text-primary ms-1"></i></h2>' +
			'<p class="text-muted mb-0" style="font-size:.9rem">' + esc(t("sign_in_hint", "Use your National ID and employee number.")) + "</p>" +
			"</div>" +
			(state.error
				? '<div class="k-alert k-alert-danger" role="alert"><i class="fa fa-exclamation-triangle"></i><span>' +
				  esc(state.error) + "</span></div>"
				: "") +
			'<label class="k-label">' + esc(t("national_id", "National ID")) + "</label>" +
			'<div class="k-input mb-3"><span class="k-input-icon"><i class="fa fa-id-card"></i></span>' +
			'<input type="text" inputmode="numeric" id="nationalId" autocomplete="off" placeholder="' +
			esc(t("national_id_ph", "e.g. 29901011234567")) + '"></div>' +
			'<label class="k-label">' + esc(t("employee_code", "Employee Number")) + "</label>" +
			'<div class="k-input mb-4"><span class="k-input-icon"><i class="fa fa-user-o"></i></span>' +
			'<input type="text" id="employeeCode" autocomplete="off" placeholder="' +
			esc(t("employee_code_ph", "e.g. V0010")) + '"></div>' +
			(recent
				? '<label class="k-label small text-muted">' + esc(t("remembered_identities", "Remembered identities")) + "</label>" +
				  '<div class="mb-4">' + recent + "</div>"
				: "") +
			(state.biometricWaiting
				? '<div class="k-alert k-alert-info" role="status"><span class="k-spin"></span><span>' +
				  esc(t("biometric_waiting", "Waiting for biometric confirmation (Face ID / fingerprint)...")) +
				  "</span></div>"
				: "") +
			'<button type="button" class="k-btn k-btn-primary k-btn-lg k-btn-block" id="signInBtn" data-autofocus' +
			(state.loading ? " disabled" : "") + ">" +
			(state.loading
				? '<span class="k-spin"></span>' + esc(t("verifying", "Verifying..."))
				: '<i class="fa fa-fingerprint"></i>' + esc(t("sign_in", "Sign In"))) +
			"</button>" +
			'<div class="k-protect"><span><i class="fa fa-shield"></i> ' + esc(t("protected_by", "Protected by")) + "</span>" +
			'<span class="k-soft-badge"><i class="fa fa-fingerprint"></i> Face ID</span>' +
			'<span class="k-soft-badge"><i class="fa fa-circle-o"></i> ' + esc(t("fingerprint", "Fingerprint")) + "</span>" +
			"</div>" +
			"</div></div></div></div>";

		var idInput = document.getElementById("nationalId");
		if (idInput) {
			idInput.focus();
			idInput.addEventListener("keyup", onLoginKeyup);
		}
		var codeInput = document.getElementById("employeeCode");
		if (codeInput) codeInput.addEventListener("keyup", onLoginKeyup);

		bind(document.getElementById("signInBtn"), "click", login);
		Array.prototype.forEach.call(document.querySelectorAll("[data-recent-id]"), function (el) {
			bind(el, "click", function () {
				pickRecent(el.dataset.recentId);
			});
		});
		Array.prototype.forEach.call(document.querySelectorAll("[data-forget-id]"), function (el) {
			bind(el, "click", function (e) {
				e.stopPropagation();
				forgetIdentity(el.dataset.forgetId);
				render();
			});
		});
	}

	function onLoginKeyup(e) {
		if (e.key === "Enter") login();
	}

	function pickRecent(nationalId) {
		var entry = state.recent.filter(function (x) {
			return x.nationalId === nationalId;
		})[0];
		if (!entry) return;
		var idInput = document.getElementById("nationalId");
		var codeInput = document.getElementById("employeeCode");
		if (idInput) idInput.value = entry.nationalId;
		if (codeInput) codeInput.value = entry.employeeCode || "";
		login();
	}

	/* ---------------------------------------------------- webauthn (Odoo) */

	/* Odoo's _b64urlEncode / _b64urlDecode. The browser's WebAuthn API wants
	 * ArrayBuffers for the challenge and credential ids, while the server speaks
	 * base64url, so the options are decoded on the way in and the response is
	 * re-encoded on the way out. */
	function b64urlEncode(arrayBuffer) {
		var bytes = new Uint8Array(arrayBuffer);
		var binary = "";
		for (var i = 0; i < bytes.length; i++) binary += String.fromCharCode(bytes[i]);
		return btoa(binary).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
	}

	function b64urlDecode(text) {
		var normalized = String(text).replace(/-/g, "+").replace(/_/g, "/");
		var padded = normalized + "=".repeat((4 - (normalized.length % 4)) % 4);
		var binary = atob(padded);
		var bytes = new Uint8Array(binary.length);
		for (var j = 0; j < binary.length; j++) bytes[j] = binary.charCodeAt(j);
		return bytes.buffer;
	}

	function webauthnSupported() {
		return !!(window.PublicKeyCredential && navigator.credentials && navigator.credentials.get);
	}

	/* Odoo's login(): always the biometric handshake, not a password. The
	 * challenge comes back as `assert` for an enrolled device and `register` for
	 * a first-time one. */
	function biometricSignIn(nationalId, employeeCode) {
		state.biometricWaiting = true;
		render();

		return call({ action: "webauthn_login", national_id: nationalId, employee_code: employeeCode })
			.then(function (wa) {
				var publicKey = wa.mode === "assert" ? wa.assertion_options : wa.attestation_options;
				if (!publicKey) throw new Error(t("no_biometric", "Biometric sign-in is not configured for your account."));

				publicKey.challenge = b64urlDecode(publicKey.challenge);
				if (publicKey.allowCredentials) {
					publicKey.allowCredentials = publicKey.allowCredentials.map(function (c) {
						return Object.assign({}, c, { id: b64urlDecode(c.id) });
					});
				}
				if (publicKey.user && publicKey.user.id) {
					publicKey.user = Object.assign({}, publicKey.user, { id: b64urlDecode(publicKey.user.id) });
				}

				var credential =
					wa.mode === "assert"
						? navigator.credentials.get({ publicKey: publicKey })
						: navigator.credentials.create({ publicKey: publicKey });

				return credential.then(function (cred) {
					var response = cred.response;
					return call({
						action: "webauthn_verify",
						preauth_token: wa.preauth_token,
						mode: wa.mode,
						client_data_json: b64urlEncode(response.clientDataJSON),
						authenticator_data: b64urlEncode(response.authenticatorData),
						credential_id: b64urlEncode(cred.rawId),
						signature: response.signature ? b64urlEncode(response.signature) : null,
						attestation_object: response.attestationObject
							? b64urlEncode(response.attestationObject)
							: null
					});
				});
			})
			.then(function (session) {
				state.biometricWaiting = false;
				return session;
			})
			.catch(function (e) {
				state.biometricWaiting = false;
				// a cancelled or unavailable prompt is the common case on a shared
				// self-service, so it gets its own actionable wording
				if (e && e.name === "NotAllowedError") {
					throw new Error(
						t(
							"biometric_cancelled",
							"Biometric authentication was cancelled or is not available on this device."
						)
					);
				}
				throw e;
			});
	}

	/* Fallback for devices with no WebAuthn at all. The server refuses this for
	 * any enrolled account, so it can only ever be the first sign-in. */
	function plainSignIn(nationalId, employeeCode) {
		return call({ action: "sign_in", national_id: nationalId, employee_code: employeeCode });
	}

	function login() {
		var nationalId = ((document.getElementById("nationalId") || {}).value || "").trim();
		var employeeCode = ((document.getElementById("employeeCode") || {}).value || "").trim();
		if (!nationalId || !employeeCode) {
			state.error = t("both_fields_required", "Enter both your national ID and employee code.");
			return render();
		}
		state.loading = true;
		state.error = "";
		render();

		var attempt = webauthnSupported()
			? biometricSignIn(nationalId, employeeCode)
			: plainSignIn(nationalId, employeeCode);

		attempt
			.then(function (session) {
				state.token = session.token;
				rememberIdentity(nationalId, employeeCode);
				return loadProfile();
			})
			.then(function () {
				state.loading = false;
				showScreen("dashboard");
			})
			.catch(function (e) {
				state.loading = false;
				state.error = e.message;
				render();
			});
	}

	/* ----------------------------------------------------------- dashboard */

	function renderDashboard() {
		var emp = state.employee || {};
		var checkedIn = isCheckedIn();
		var approvals = state.approvalCount || 0;

		host.innerHTML =
			'<div class="k-screen-center k-fade text-center">' +
			'<div class="k-stack">' +

			/* avatar + live status dot */
			'<div class="k-avatar-wrap">' +
			(emp.avatar
				? '<img src="' + esc(emp.avatar) + '" class="k-avatar" alt="" width="112" height="112">'
				: '<div class="k-avatar-ph"><i class="fa fa-user"></i></div>') +
			'<span class="k-status-dot' + (checkedIn ? "" : " k-status-dot--out") + '"></span>' +
			"</div>" +

			'<div class="k-greeting">' + esc(greeting()) + "</div>" +
			'<h1 class="k-emp-name">' + esc(emp.employee_name || "") + "</h1>" +
			(emp.employee_code
				? '<div class="mb-2"><span class="k-code-chip"><i class="fa fa-id-card-o me-1"></i>' +
				  esc(emp.employee_code) + "</span></div>"
				: "") +
			'<div class="mb-4"><span class="k-badge-state ' + (checkedIn ? "k-badge-state--in" : "k-badge-state--out") + '">' +
			'<i class="fa ' + (checkedIn ? "fa-sign-in" : "fa-sign-out") + '"></i>' +
			esc(checkedIn ? t("checked_in", "Checked In") : t("checked_out", "Checked Out")) +
			"</span></div>" +

			/* Odoo's three dashboard stat cards */
			'<div class="k-stats">' +
			'<div class="k-stat"><span class="k-stat-accent"></span>' +
			'<div class="k-stat-label">' + esc(t("today", "Today")) + "</div>" +
			'<div class="k-stat-value k-tabular">' + esc(formatHours(emp.hours_today)) + "</div></div>" +
			'<div class="k-stat"><span class="k-stat-accent k-stat-accent--success"></span>' +
			'<div class="k-stat-label">' + esc(t("last_in", "Last In")) + "</div>" +
			'<div class="k-stat-value k-stat-value--sm k-tabular">' + esc(formatTime(emp.last_check_in)) + "</div></div>" +
			'<div class="k-stat"><span class="k-stat-accent k-stat-accent--purple"></span>' +
			'<div class="k-stat-label">' + esc(t("session", "Session")) + "</div>" +
			'<div class="k-stat-value k-stat-value--sm k-tabular">' +
			esc(formatHours(emp.last_attendance_worked_hours)) + "</div></div>" +
			"</div>" +

			(state.error
				? '<div class="k-alert k-alert-danger" role="alert"><i class="fa fa-exclamation-triangle"></i><span>' +
				  esc(state.error) + "</span></div>"
				: "") +
			(state.geoWaiting
				? '<div class="k-alert k-alert-info" role="status"><span class="k-spin"></span><span>' +
				  esc(t("getting_location", "Getting your location...")) + "</span></div>"
				: "") +

			/* punch card: gradient header, IN/OUT mini tiles, single toggle button */
			'<div class="k-punch-card">' +
			'<div class="k-punch-head">' +
			'<span class="fw-bold"><i class="fa fa-clock-o me-2"></i>' + esc(t("attendance_today", "Attendance Today")) + "</span>" +
			'<span class="k-punch-head-date"><i class="fa fa-calendar-o me-1"></i>' + esc(clockDate()) + "</span>" +
			"</div>" +
			'<div class="k-card-body">' +
			'<div class="k-mini-row">' +
			'<div class="k-mini"><div class="k-mini-label"><i class="fa fa-sign-in"></i> ' +
			esc(t("checked_in_label", "Checked In")) + '</div><div class="k-mini-value k-mini-value--in k-tabular">' +
			esc(formatTime(emp.last_check_in)) + "</div></div>" +
			'<div class="k-mini"><div class="k-mini-label"><i class="fa fa-sign-out"></i> ' +
			esc(t("checked_out_label", "Checked Out")) + '</div><div class="k-mini-value k-mini-value--out k-tabular">' +
			esc(formatTime(emp.last_check_out)) + "</div></div>" +
			"</div>" +
			'<button type="button" class="k-btn k-btn-lg k-btn-block ' +
			(checkedIn ? "k-btn-danger" : "k-btn-success") + '" id="punchBtn"' +
			(state.loading ? " disabled" : "") + ">" +
			(state.loading
				? '<span class="k-spin"></span>'
				: '<i class="fa ' + (checkedIn ? "fa-sign-out" : "fa-sign-in") + '"></i>') +
			esc(checkedIn ? t("check_out", "Check Out") : t("check_in", "Check In")) +
			"</button>" +
			'<div class="k-loc-note"><i class="fa fa-map-marker"></i> ' +
			esc(t("location_note", "Your location is captured securely with this entry.")) + "</div>" +
			"</div></div>" +

			/* menu tiles */
			'<div class="k-tiles">' +
			tile("time_off", "timeoff", "fa-calendar-check-o", "Time Off") +
			tile("my_requests", "list", "fa-list-alt", "My Requests") +
			tile("payslips", "payslip", "fa-file-pdf-o", "My Payslips") +
			tile("approvals", "request", "fa-pencil-square-o", approvals > 0
				? t("approvals", "Approvals") + " (" + approvals + ")"
				: t("approvals", "Approvals")) +
			"</div>" +

			'<div class="text-center mt-4">' +
			'<button type="button" class="k-btn k-btn-ghost" id="endSessionBtn"><i class="fa fa-power-off"></i> ' +
			esc(t("end_session", "End Session")) + "</button>" +
			"</div>" +

			"</div></div>";

		bind(document.getElementById("punchBtn"), "click", toggleAttendance);
		bind(document.getElementById("endSessionBtn"), "click", resetToLogin);
		Array.prototype.forEach.call(host.querySelectorAll("[data-tile]"), function (el) {
			bind(el, "click", function () {
				openPanel(el.dataset.tile);
			});
		});
	}

	function tile(action, variant, icon, label) {
		return (
			'<button type="button" class="k-tile" data-tile="' + esc(action) + '">' +
			'<span class="k-tile-icon k-tile-icon--' + esc(variant) + '"><i class="fa ' + esc(icon) + '"></i></span>' +
			'<span class="k-tile-label">' + esc(label) + "</span></button>"
		);
	}

	function loadProfile() {
		return call({ action: "my_profile", token: state.token }).then(function (profile) {
			state.employee = profile;
			state.selfServiceDelay = profile.self_service_delay || state.inactivityMs;
			return profile;
		});
	}

	/* Odoo's toggleAttendance: one button that flips IN/OUT from live state. */
	function toggleAttendance() {
		var logType = isCheckedIn() ? "OUT" : "IN";
		state.loading = true;
		state.geoWaiting = true;
		state.error = "";
		render();

		var position = null;
		if (navigator.geolocation) {
			position = new Promise(function (resolve) {
				navigator.geolocation.getCurrentPosition(
					function (pos) {
						resolve({ lat: pos.coords.latitude, lon: pos.coords.longitude });
					},
					function () {
						resolve(null);
					},
					{ timeout: 8000, maximumAge: 60000, enableHighAccuracy: true }
				);
			});
		} else {
			position = Promise.resolve(null);
		}

		position
			.then(function (coords) {
				state.geoWaiting = false;
				var payload = { action: "punch", token: state.token, log_type: logType };
				if (coords) {
					payload.latitude = coords.lat;
					payload.longitude = coords.lon;
				}
				return call(payload);
			})
			.then(function (data) {
				state.loading = false;
				if (data && data.geo && data.geo.error) {
					state.error = data.geo.error;
					return loadProfile().then(function () {
						render();
					});
				}
				state.greet = { action: logType === "IN" ? "check_in" : "check_out", time: data.time || "" };
				return loadProfile().then(function () {
					showScreen("greet");
				});
			})
			.catch(function (e) {
				state.loading = false;
				state.geoWaiting = false;
				state.error = e.message;
				render();
			});
	}

	/* -------------------------------------------------------------- greet */

	function renderGreet() {
		var isIn = state.greet.action === "check_in";
		var confetti = "";
		if (isIn) {
			for (var i = 0; i < CONFETTI; i++) {
				confetti += '<span class="k-confetti k-confetti-' + i + '"></span>';
			}
		}
		host.innerHTML =
			'<div class="k-screen-center k-fade text-center" style="position:relative">' +
			confetti +
			'<div class="k-greet-ring mb-3"><span class="k-pulse"></span>' +
			'<i class="fa ' + (isIn ? "fa-circle-check" : "fa-arrow-circle-right") + " k-greet-icon" +
			(isIn ? " text-success" : " text-primary") + '"></i></div>' +
			'<h1 class="k-greet-title fw-bold mb-1">' +
			esc(isIn ? t("welcome", "Welcome") : t("goodbye", "Goodbye")) + "</h1>" +
			'<h2 class="fw-bold text-truncate mb-3" style="font-size:1.5rem">' +
			esc((state.employee || {}).employee_name || "") + "</h2>" +
			'<p class="lead text-muted mb-4" style="font-size:1.05rem">' +
			esc(isIn ? t("checked_in_at", "Checked in at") : t("checked_out_at", "Checked out at")) + "</p>" +
			'<div class="k-time-card mb-4">' +
			'<div class="k-time-card-label">' + esc(t("time", "Time")) + "</div>" +
			'<div class="k-time-card-value k-tabular">' + esc(formatTime(state.greet.time)) + "</div></div>" +
			'<div><button type="button" class="k-btn k-btn-primary k-btn-pill k-btn-lg" id="greetOk">' +
			'<i class="fa fa-arrow-right"></i> OK</button></div>' +
			"</div>";
		bind(document.getElementById("greetOk"), "click", function () {
			showScreen("dashboard");
		});
	}

	/* A short spoken label for each screen, used by the aria-live region. */
	function screenTitle(screen) {
		return {
			login: t("employee_sign_in", "Employee Sign In"),
			dashboard: (state.employee || {}).employee_name || t("employee_self_service", "Employee Self Service"),
			greet: t("welcome", "Welcome"),
			payslips: t("my_payslips", "My Payslips"),
			approvals: t("approvals", "Approvals"),
			my_requests: t("my_requests", "My Requests"),
			time_off: t("time_off", "Time Off")
		}[screen] || t("menu", "Menu");
	}

	/* Menu tiles open one of the list screens. The tile's data-tile is a *screen*
	 * name; renderPanel() maps it to the right API action. */
	function openPanel(screen) {
		// a fresh screen starts unfiltered; a filter or grouping left over from the
		// last panel would silently hide or rearrange rows
		state.listFilter = "all";
		state.listQuery = "";
		state.listGroup = false;
		showScreen(screen);
	}

	/* ------------------------------------------------------------- panels */

	/* The list screens each return their rows under their own envelope key, so
	 * the unwrapping has to be explicit. Reading a fixed `data.rows` (as an
	 * earlier version did) meant `my_leaves` -> {"leaves": [...]} always
	 * rendered as "no requests", even with ten of them on file. */
	var LIST_KEYS = { payslips: "payslips", approvals: "approvals", my_requests: "leaves" };

	function listRows(action, data) {
		if (Array.isArray(data)) return data;
		if (!data || typeof data !== "object") return [];
		var key = LIST_KEYS[action];
		return key && Array.isArray(data[key]) ? data[key] : [];
	}

	/* Odoo gives each screen a coloured head icon (payslip orange, list purple,
	 * request pink) rather than one flat blue for all of them. */
	function panelHead(action, icon, title) {
		var variant = {
			payslips: "k-tile-icon--payslip",
			my_requests: "k-tile-icon--list",
			approvals: "k-tile-icon--request",
			time_off: "k-tile-icon--timeoff"
		}[action] || "";
		return (
			'<div class="k-screen-head">' +
			'<button type="button" class="k-back" id="panelBack" data-autofocus><i class="fa fa-arrow-left"></i> ' +
			esc(t("back_to_dashboard", "Back to Dashboard")) + "</button>" +
			'<span class="k-screen-icon ' + variant + '"><i class="fa ' + esc(icon) + '"></i></span>' +
			'<span><span class="k-screen-title d-block">' + esc(title) + "</span>" +
			'<span class="k-screen-sub d-block">' + esc((state.employee || {}).employee_name || "") + "</span></span>" +
			"</div>"
		);
	}

	function renderPanel(action) {
		host.innerHTML =
			'<div class="k-screen k-fade">' +
			panelHead(action, panelIcon(action), panelTitle(action)) +
			'<div class="k-screen-body" id="panelBody"></div></div>';
		bind(document.getElementById("panelBack"), "click", function () {
			showScreen("dashboard");
		});
		if (action === "time_off") {
			loadTimeOff();
		} else {
			loadPanel(action);
		}
	}

	/* ------------------------------------------------------------ time off */

	/* Odoo's KioskTimeOff: the vacation balance card, a searchable grid of
	 * leave-type cards, and (once a type is picked) the request form with its
	 * live leave summary. */
	function loadTimeOff() {
		var body = document.getElementById("panelBody");
		if (!body) return;
		state.timeOffForm = null;
		state.timeOffQuery = "";
		state.timeOffOnlyAvailable = false;
		body.innerHTML = '<div class="text-center py-4 text-muted"><span class="k-spin"></span></div>';

		Promise.all([
			call({ action: "leave_balances", token: state.token }),
			call({ action: "leave_types", token: state.token })
		])
			.then(function (res) {
				state.timeOffBalances = res[0] || {};
				state.timeOffTypes = (res[1] && res[1].leave_types) || [];
				renderTimeOff(body);
			})
			.catch(function (e) {
				body.innerHTML =
					'<div class="k-alert k-alert-danger"><i class="fa fa-exclamation-triangle"></i><span>' +
					esc(e.message) + "</span></div>";
			});
	}

	function renderTimeOff(body) {
		if (state.timeOffForm) return renderTimeOffForm(body);

		var balances = state.timeOffBalances || {};
		var types = state.timeOffTypes || [];
		var html = "";

		/* my-requests shortcut, like Odoo's screen head */
		html +=
			'<div class="d-flex justify-content-end gap-2 mb-3">' +
			'<button type="button" class="k-pill-btn" id="timeOffMyRequests" data-autofocus><i class="fa fa-list-alt"></i> ' +
			esc(t("my_requests", "My Requests")) + "</button></div>";

		/* The balance card. One pooled entitlement, because that is how the tracker
		 * workbook models it - not one bucket per leave type. */
		if (balances.balances && balances.balances.length) {
			var context = balances.context || {};
			html +=
				'<div class="k-punch-card mb-4"><div class="k-card-body">' +
				'<div class="d-flex align-items-center justify-content-between mb-3">' +
				'<span class="fw-bold"><i class="fa fa-balance-scale"></i> ' +
				esc(t("vacation_balance", "Vacation Balance")) + "</span>" +
				'<span class="text-muted small">' + esc(balances.year || "") + "</span></div>";
			balances.balances.forEach(function (row) {
				/* Leave in lieu is a net adjustment rather than an allowance, and it can
				 * push the balance past the entitlement, so it gets no progress bar -
				 * which would otherwise read over 100%. */
				if (row.code === "LIEU") {
					html +=
						'<div class="d-flex justify-content-between align-items-center py-2">' +
						'<span class="fw-semibold small">' + esc(row.label) + "</span>" +
						'<span class="text-muted small">' +
						(row.remaining > 0 ? "+" : "") + esc(formatDays(row.remaining)) + " " +
						esc(t("days", "days")) + "</span></div>";
					if (row.breakdown && row.breakdown.length) {
						html += '<div class="text-muted small ps-3 pb-2">';
						row.breakdown.forEach(function (part) {
							html +=
								'<div class="d-flex justify-content-between"><span>' +
								esc(part.label) + "</span><span>" +
								(part.days > 0 ? "+" : "") + esc(formatDays(part.days)) +
								"</span></div>";
						});
						html += "</div>";
					}
					return;
				}

				var pct = balancePercent(row);
				var tone = pct <= 20 ? " k-progress-bar--empty" : pct <= 50 ? " k-progress-bar--low" : "";
				/* "calculated" marks an entitlement computed from the workbook's own rule
				 * rather than one HR entered, so it is not read as a recorded figure. */
				var derived = row.derived
					? ' <span class="k-badge-state" style="font-size:.7rem;padding:.1rem .45rem">' +
					  esc(t("calculated", "calculated")) + "</span>"
					: "";
				html +=
					'<div class="py-2">' +
					'<div class="d-flex justify-content-between align-items-center mb-1">' +
					'<span class="fw-semibold small">' + esc(row.label || row.code) + derived + "</span>" +
					'<span class="k-badge-state ' + (row.remaining > 0 ? "k-badge-state--in" : "k-badge-state--out") +
					'" style="font-size:.78rem;padding:.15rem .6rem">' + esc(formatDays(row.remaining)) + " " +
					esc(t("left", "left")) + "</span></div>" +
					'<div class="d-flex align-items-center gap-2">' +
					'<div class="k-progress" style="flex:1 1 auto"><div class="k-progress-bar' + tone +
					'" style="width:' + pct + '%"></div></div>' +
					'<span class="text-muted small" style="min-width:5rem;text-align:right">' +
					esc(formatDays(row.used)) + " / " + esc(formatDays(row.due)) + "</span>" +
					"</div>";

				/* Carried-forward days expire, and the workbook's own header caps them at
				 * 10 - which 14 employees exceed. Both are worth saying out loud. */
				if (row.code === "CF") {
					if (row.expires) {
						html +=
							'<div class="text-muted small ps-3">' + esc(t("expires", "Expires")) + " " +
							esc(formatDate(row.expires)) + "</div>";
					}
					if (row.above_documented_cap) {
						html +=
							'<div class="text-muted small ps-3">' +
							esc(t("carried_above_cap_note",
								"Above the 10-day carry-forward limit recorded in the tracker.")) +
							"</div>";
					}
				}
				if (row.breakdown && row.breakdown.length) {
					html += '<div class="text-muted small ps-3 pb-1">';
					row.breakdown.forEach(function (part) {
						html +=
							'<div class="d-flex justify-content-between"><span>' + esc(part.label) +
							"</span><span>" + esc(formatDays(part.days)) + "</span></div>";
					});
					html += "</div>";
				}
				html += "</div>";
			});

			/* Where the figures came from, and as of when. Without this a snapshot of a
			 * hand-maintained workbook reads as a live calculation. */
			if (context.workbook) {
				html +=
					'<div class="text-muted small pt-2 mt-2 border-top">' + esc(t("balance_source", "From")) +
					" " + esc(context.workbook + " / " + (context.sheet || "")) +
					(context.as_of ? ", " + esc(t("as_of", "as of")) + " " + esc(formatDate(context.as_of)) : "") +
					"</div>";
			}
			html += "</div></div>";
		} else if (balances.balances) {
			/* Eligible is false, so the workbook records no balance at all. Showing 0
			 * would claim they are owed nothing, which is a different statement. */
			html +=
				'<div class="k-punch-card mb-4"><div class="k-card-body">' +
				'<div class="fw-bold mb-2"><i class="fa fa-balance-scale"></i> ' +
				esc(t("vacation_balance", "Vacation Balance")) + "</div>" +
				'<div class="text-muted small">' +
				esc(t("no_entitlement_recorded",
					"No leave entitlement has been recorded for you yet. Please contact HR.")) +
				"</div></div></div>";
		}

		if (!types.length) {
			html +=
				'<div class="k-empty py-5"><div class="k-empty-icon"><i class="fa fa-calendar-o"></i></div>' +
				'<div class="k-empty-title">' + esc(t("no_time_off", "No time-off types are available yet.")) +
				"</div>" +
				'<div class="text-muted" style="font-size:.85rem">' +
				esc(t("no_time_off_sub", "Ask HR to add a leave type and allocation.")) + "</div></div>";
			body.innerHTML = html;
			bindTimeOffHead(body);
			return;
		}

		var visible = filteredTimeOffTypes();

		/* search + "available only" toggle */
		html +=
			'<div class="k-filter-bar">' +
			'<div class="k-search" style="max-width:100%;flex:1 1 260px"><i class="fa fa-search"></i>' +
			'<input type="search" id="timeOffSearch" placeholder="' +
			esc(t("search_time_off", "Search time-off types...")) + '" value="' + esc(state.timeOffQuery) + '"></div>' +
			'<button type="button" class="k-chip' + (state.timeOffOnlyAvailable ? " active" : "") +
			'" id="timeOffAvailToggle" title="' + esc(t("only_available", "Show only types with remaining balance")) + '">' +
			'<i class="fa ' + (state.timeOffOnlyAvailable ? "fa-check-circle-o" : "fa-circle-o") + '"></i> ' +
			esc(state.timeOffOnlyAvailable ? t("available_only", "Available only") : t("all_types", "All types")) +
			"</button>" +
			'<span class="text-muted small" style="margin-inline-start:auto">' + visible.length + " / " +
			types.length + "</span></div>";

		if (!visible.length) {
			html +=
				'<div class="k-empty py-5"><div class="k-empty-icon"><i class="fa fa-filter"></i></div>' +
				'<div class="k-empty-title">' + esc(t("no_match", "No time-off types match your filters.")) + "</div>" +
				'<button type="button" class="k-pill-btn mt-2" id="timeOffClear"><i class="fa fa-undo"></i> ' +
				esc(t("clear_filters", "Clear filters")) + "</button></div>";
		} else {
			html += '<div class="k-cats">';
			visible.forEach(function (type) {
				var left = Number(type.virtual_remaining_leaves) || 0;
				html +=
					'<button type="button" class="k-cat" data-type="' + esc(type.name) + '">' +
					'<span class="k-card-accent-sm"></span>' +
					'<span class="d-flex align-items-center mb-2">' +
					'<span class="k-brand-logo" style="width:34px;height:34px;flex:0 0 34px;border-radius:8px">' +
					'<i class="fa fa-calendar-check-o"></i></span>' +
					'<span class="flex-grow-1" style="min-width:0">' +
					'<span class="d-block fw-bold text-truncate">' + esc(type.name) + "</span>" +
					(type.vacation_code
						? '<span class="k-code-chip">' + esc(type.vacation_code) + "</span>"
						: "") +
					"</span></span>" +
					'<span class="d-flex align-items-center justify-content-between text-muted" style="font-size:.78rem">' +
					"<span><i class=\"fa fa-arrows-v\"></i> " +
					(left > 0 ? esc(formatDays(left)) + " " + esc(t("left", "left")) : esc(t("no_balance", "No balance"))) +
					"</span></span>" +
					'<i class="fa fa-chevron-circle-right k-cat-arrow"></i></button>';
			});
			html += "</div>";
		}

		body.innerHTML = html;
		bindTimeOffHead(body);

		bind(document.getElementById("timeOffSearch"), "input", function (e) {
			state.timeOffQuery = e.target.value;
			renderTimeOff(body);
			var s = document.getElementById("timeOffSearch");
			if (s) {
				s.focus();
				s.setSelectionRange(s.value.length, s.value.length);
			}
		});
		bind(document.getElementById("timeOffAvailToggle"), "click", function () {
			state.timeOffOnlyAvailable = !state.timeOffOnlyAvailable;
			renderTimeOff(body);
		});
		bind(document.getElementById("timeOffClear"), "click", function () {
			state.timeOffQuery = "";
			state.timeOffOnlyAvailable = false;
			renderTimeOff(body);
		});
		Array.prototype.forEach.call(body.querySelectorAll("[data-type]"), function (el) {
			bind(el, "click", function () {
				state.timeOffForm = {
					leave_type: el.dataset.type,
					from_date: "",
					to_date: "",
					reason: ""
				};
				renderTimeOff(body);
			});
		});
	}

	function bindTimeOffHead(body) {
		bind(document.getElementById("timeOffMyRequests"), "click", function () {
			showScreen("my_requests");
		});
	}

	function filteredTimeOffTypes() {
		var needle = (state.timeOffQuery || "").trim().toLowerCase();
		return (state.timeOffTypes || []).filter(function (type) {
			if (needle && String(type.name).toLowerCase().indexOf(needle) < 0) return false;
			if (state.timeOffOnlyAvailable && !(Number(type.virtual_remaining_leaves) > 0)) return false;
			return true;
		});
	}

	function balancePercent(row) {
		if (!row || !row.due) return 0;
		return Math.max(0, Math.min(100, Math.round((row.remaining / row.due) * 100)));
	}

	function formatDays(value) {
		return Number(value || 0).toLocaleString(intlLocale(), { maximumFractionDigits: 2 });
	}

	/* Odoo's timeOffDays(): inclusive working days between the two dates. */
	function timeOffDays() {
		var form = state.timeOffForm;
		if (!form || !form.from_date || !form.to_date) return 0;
		var from = parseDate(form.from_date);
		var to = parseDate(form.to_date);
		if (!from || !to || to < from) return 0;
		var days = 0;
		var cur = new Date(from.getFullYear(), from.getMonth(), from.getDate());
		var end = new Date(to.getFullYear(), to.getMonth(), to.getDate());
		while (cur <= end) {
			var dow = cur.getDay();
			if (dow !== 5 && dow !== 6) days += 1;
			cur.setDate(cur.getDate() + 1);
		}
		return days;
	}

	function remainingForType(name) {
		var row = (state.timeOffTypes || []).filter(function (x) {
			return x.name === name;
		})[0];
		return row ? Number(row.virtual_remaining_leaves) || 0 : 0;
	}

	function renderTimeOffForm(body) {
		var form = state.timeOffForm;
		var type = (state.timeOffTypes || []).filter(function (x) {
			return x.name === form.leave_type;
		})[0] || {};
		var days = timeOffDays();
		var left = remainingForType(form.leave_type);
		var enough = days > 0 && days <= left;

		// `var html` -- assigning this without a declaration is a ReferenceError
		// under the "use strict" at the top of this file, which killed the whole
		// leave request form.
		var html = '<button type="button" class="k-back mb-2" id="timeOffBack"><i class="fa fa-arrow-left"></i> ' +
			esc(t("all_time_off_types", "All Time Off Types")) + "</button>" +
			'<div class="k-card"><span class="k-card-accent"></span><div class="k-card-body">' +
			'<div class="d-flex align-items-center mb-3">' +
			'<span class="k-brand-mark" style="width:56px;height:56px;font-size:1.3rem;border-radius:14px">' +
			'<i class="fa fa-calendar-check-o"></i></span>' +
			'<span><span class="d-block fw-bold" style="font-size:1.15rem">' + esc(type.name || form.leave_type) +
			'</span><span class="text-muted" style="font-size:.8rem">' +
			esc((state.employee || {}).employee_name || "") + "</span></span></div>" +
			(state.error
				? '<div class="k-alert k-alert-danger"><i class="fa fa-exclamation-triangle"></i><span>' +
				  esc(state.error) + "</span></div>"
				: "") +
			'<div class="k-mini-row">' +
			'<div><label class="k-label">' + esc(t("from", "From")) + "</label>" +
			'<div class="k-input"><span class="k-input-icon"><i class="fa fa-calendar-o"></i></span>' +
			'<input type="date" id="leaveFrom" value="' + esc(form.from_date) + '"></div></div>' +
			'<div><label class="k-label">' + esc(t("to", "To")) + "</label>" +
			'<div class="k-input"><span class="k-input-icon"><i class="fa fa-calendar-check-o"></i></span>' +
			'<input type="date" id="leaveTo" value="' + esc(form.to_date) + '"></div></div>' +
			"</div>" +
			'<div class="mb-3"><label class="k-label">' + esc(t("description", "Description")) + "</label>" +
			'<div class="k-input" style="align-items:flex-start"><span class="k-input-icon">' +
			'<i class="fa fa-align-left"></i></span>' +
			'<textarea id="leaveReason" rows="3" style="resize:vertical">' + esc(form.reason) + "</textarea></div></div>";

		if (days > 0) {
			html +=
				'<div class="k-recap' + (left > 0 && !enough ? " k-recap--warn" : "") + ' mb-3">' +
				'<div class="fw-bold text-uppercase k-tracking mb-2" style="font-size:.72rem">' +
				'<i class="fa fa-calculator"></i> ' + esc(t("leave_summary", "Leave Summary")) + "</div>" +
				'<div class="d-flex flex-column gap-1">' +
				recapLine("fa-calendar-check-o", t("type", "Type"), esc(type.name || form.leave_type)) +
				recapLine(
					"fa-calendar-o",
					t("period", "Period"),
					esc(formatDate(form.from_date)) + (form.to_date ? " &rarr; " + esc(formatDate(form.to_date)) : "")
				) +
				'<div class="d-flex align-items-center text-muted" style="font-size:.8rem">' +
				'<i class="fa fa-arrows-v k-text-primary me-2"></i>' +
				'<span class="fw-semibold me-2 text-dark" style="min-width:110px">' + esc(t("duration", "Duration")) +
				'</span><span class="k-days-badge">' + days + " " + esc(days > 1 ? t("days", "days") : t("day", "day")) +
				"</span></div>";
			if (left > 0) {
				html +=
					'<div class="d-flex align-items-center mt-1" style="font-size:.8rem">' +
					'<i class="fa fa-balance-scale me-2"></i>' + esc(formatDays(left)) + " " + esc(t("days_left", "days left")) +
					(enough
						? '<span class="ms-2" style="color:#059669"><i class="fa fa-check-circle-o"></i> ' +
						  esc(t("enough_balance", "Enough balance")) + "</span>"
						: '<span class="ms-2" style="color:#dc2626"><i class="fa fa-exclamation-circle"></i> ' +
						  esc(t("exceeds_balance", "Exceeds balance")) + "</span>") +
					"</div>";
			}
			html += "</div></div>";
		}

		html +=
			'<button type="button" class="k-btn k-btn-primary k-btn-lg k-btn-block" id="leaveSubmit"' +
			(state.loading ? " disabled" : "") + ">" +
			(state.loading
				? '<span class="k-spin"></span>' + esc(t("submitting", "Submitting..."))
				: '<i class="fa fa-paper-plane"></i>' + esc(t("submit_request", "Submit Request"))) +
			"</button></div></div>";

		body.innerHTML = html;

		bind(document.getElementById("timeOffBack"), "click", function () {
			state.timeOffForm = null;
			state.error = "";
			renderTimeOff(body);
		});
		bind(document.getElementById("leaveFrom"), "change", function (e) {
			state.timeOffForm.from_date = e.target.value;
			renderTimeOffForm(body);
		});
		bind(document.getElementById("leaveTo"), "change", function (e) {
			state.timeOffForm.to_date = e.target.value;
			renderTimeOffForm(body);
		});
		bind(document.getElementById("leaveReason"), "input", function (e) {
			state.timeOffForm.reason = e.target.value;
		});
		bind(document.getElementById("leaveSubmit"), "click", submitLeaveRequest);
	}

	function recapLine(icon, label, value) {
		return (
			'<div class="d-flex align-items-center text-muted" style="font-size:.8rem">' +
			'<i class="fa ' + esc(icon) + " k-text-primary me-2\"></i>" +
			'<span class="fw-semibold me-2 text-dark" style="min-width:110px">' + esc(label) + "</span>" +
			value + "</div>"
		);
	}

	function submitLeaveRequest() {
		var form = state.timeOffForm;
		if (!form || !form.from_date || !form.to_date) {
			state.error = t("pick_dates", "Choose both a start and an end date.");
			return renderTimeOffForm(document.getElementById("panelBody"));
		}
		state.loading = true;
		state.error = "";
		renderTimeOffForm(document.getElementById("panelBody"));

		call({
			action: "request_leave",
			token: state.token,
			leave_type: form.leave_type,
			from_date: form.from_date,
			to_date: form.to_date,
			reason: form.reason || ""
		})
			.then(function () {
				state.loading = false;
				state.timeOffForm = null;
				showScreen("my_requests");
			})
			.catch(function (e) {
				state.loading = false;
				state.error = e.message;
				renderTimeOffForm(document.getElementById("panelBody"));
			});
	}

	function panelIcon(action) {
		return {
			payslips: "fa-file-pdf-o",
			approvals: "fa-inbox",
			my_requests: "fa-list-alt",
			time_off: "fa-calendar-check-o"
		}[action] || "fa-folder";
	}

	function panelTitle(action) {
		return {
			payslips: t("my_payslips", "My Payslips"),
			approvals: t("approvals", "Approvals"),
			my_requests: t("my_requests", "My Requests"),
			time_off: t("time_off", "Time Off")
		}[action] || t("menu", "Menu");
	}

	/* the API action backing each panel; the screen name and the action name
	 * differ, so they are mapped explicitly */
	function panelApiAction(screen) {
		return {
			payslips: "payslips",
			approvals: "approvals",
			my_requests: "my_leaves"
		}[screen] || screen;
	}

	function loadPanel(action) {
		var body = document.getElementById("panelBody");
		if (!body) return;
		body.innerHTML = '<div class="text-center py-4 text-muted"><span class="k-spin"></span></div>';
		call({ action: panelApiAction(action), token: state.token })
			.then(function (data) {
				body.innerHTML = renderList(action, data);
				bindPanelEvents(action, data);
			})
			.catch(function (e) {
				body.innerHTML =
					'<div class="k-alert k-alert-danger"><i class="fa fa-exclamation-triangle"></i><span>' +
					esc(e.message) + "</span></div>" + emptyBlock("fa-exclamation-triangle", t("error", "Error"), "");
			});
	}

	function emptyBlock(icon, title, sub) {
		return (
			'<div class="k-empty"><div class="k-empty-icon"><i class="fa ' + esc(icon) + '"></i></div>' +
			'<div class="k-empty-title">' + esc(title) + "</div>" +
			'<div class="text-muted" style="font-size:.85rem">' + esc(sub || "") + "</div></div>"
		);
	}

	/* Odoo's slipRowAccent / requestRowAccent: a wider status vocabulary than the
	 * four tones this started with (done, paid, cancel, refuse, draft, doing,
	 * confirm, approved, rejected, submitted, open, pending). */
	var ACCENTS = {
		done: "success", paid: "success", approved: "success", submitted: "success",
		confirm: "info", doing: "info", draft: "info", pending: "info", open: "info",
		refuse: "danger", rejected: "danger", cancel: "danger", cancelled: "danger",
		secondary: "secondary"
	};

	function rowTone(status) {
		var s = String(status || "").toLowerCase();
		if (ACCENTS[s]) return "k-row--" + ACCENTS[s];
		// fall back to a substring match for statuses we have not seen
		if (/approv|submit|paid|done|accept/.test(s)) return "k-row--success";
		if (/reject|refus|cancel/.test(s)) return "k-row--danger";
		if (/pending|open|draft|doing|confirm/.test(s)) return "k-row--info";
		return "k-row--light";
	}

	/* Odoo's per-screen row shapes. Each one names its title, the meta line
	 * beneath it, and the glyph Odoo shows in the row's leading icon. */
	var LIST_VIEWS = {
		payslips: {
			icon: "fa-file-pdf-o",
			title: function (r) {
				return r.start_date
					? formatDate(r.start_date) + " \u2013 " + formatDate(r.end_date)
					: r.name;
			},
			meta: function (r) {
				var out = [];
				if (r.net_pay !== undefined && r.net_pay !== null) {
					out.push({ icon: "fa-money", text: formatMoney(r.net_pay) + " " + (r.currency || "") });
				}
				return out;
			},
			empty: ["fa-file-pdf-o", t("no_payslips", "No payslips found"),
				t("no_payslips_sub", "Payslips appear here once a payroll has been submitted for you.")],
			/* Odoo groups payslips by year: fa-calendar chip, List/Grouped toggle */
			group: {
				icon: "fa-calendar",
				variant: "k-tile-icon--payslip",
				key: function (r) {
					return String(r.start_date || r.posting_date || "").slice(0, 4) || t("no_date", "No date");
				}
			}
		},
		approvals: {
			icon: "fa-inbox",
			title: function (r) { return r.employee_name || r.employee || r.name; },
			meta: function (r) {
				var out = [];
				if (r.leave_type) out.push({ icon: "fa-calendar-check-o", text: r.leave_type });
				if (r.from_date) {
					out.push({
						icon: "fa-calendar-o",
						text: formatDate(r.from_date) + (r.to_date ? " \u2013 " + formatDate(r.to_date) : "")
					});
				}
				if (r.total_leave_days) {
					out.push({ icon: "fa-arrows-v", text: r.total_leave_days + " " + t("days", "days") });
				}
				return out;
			},
			empty: ["fa-inbox", t("no_approvals", "Nothing to approve"),
				t("no_approvals_sub", "Requests waiting for your approval will appear here.")],
			note: t("approve_at_desk", "Approve or reject these from the HR desk (Leave Application list).")
		},
		my_requests: {
			icon: "fa-list-alt",
			title: function (r) { return r.leave_type || r.name; },
			meta: function (r) {
				var out = [];
				if (r.from_date) {
					out.push({
						icon: "fa-calendar-o",
						text: formatDate(r.from_date) + (r.to_date ? " \u2013 " + formatDate(r.to_date) : "")
					});
				}
				if (r.total_leave_days) {
					out.push({ icon: "fa-arrows-v", text: r.total_leave_days + " " + t("days", "days") });
				}
				if (r.posting_date) out.push({ icon: "fa-clock-o", text: formatDate(r.posting_date) });
				return out;
			},
			empty: ["fa-list-alt", t("no_requests", "No requests yet"),
				t("no_requests_sub", "Requests you submit from this self-service page will appear here.")],
			/* Odoo groups requests by category: fa-folder-open chip, List/Grouped toggle */
			group: {
				icon: "fa-folder-open",
				variant: "k-tile-icon--list",
				key: function (r) {
					return r.leave_type || t("uncategorised", "Uncategorised");
				}
			}
		}
	};

	function renderList(action, data) {
		var rows = listRows(action, data);
		var view = LIST_VIEWS[action] || { icon: "fa-folder-open", empty: ["fa-folder-open", t("nothing_here", "Nothing here"), ""] };
		var rawSearch = state.listQuery || "";
		// the needle has to be folded too: the haystack is lowercased, so
		// "casual leave".indexOf("Casual") is -1 and the search matches nothing
		var search = rawSearch.trim().toLowerCase();
		var filter = state.listFilter || "all";

		if (!rows.length) return emptyBlock(view.empty[0], view.empty[1], view.empty[2]);

		/* Odoo's filter bar: status chips (with counts), a group toggle, the row
		 * count, and search. */
		var statuses = [];
		rows.forEach(function (r) {
			var s = r.status || r.state || "";
			if (s && statuses.indexOf(s) < 0) statuses.push(s);
		});

		var visible = rows.filter(function (r) {
			if (filter !== "all" && (r.status || r.state || "") !== filter) return false;
			if (!search) return true;
			return (rowText(r) + " " + (r.status || "")).toLowerCase().indexOf(search) >= 0;
		});

		var html = '<div class="k-filter-bar">';
		html +=
			'<button type="button" class="k-chip' + (filter === "all" ? " active" : "") +
			'" data-filter="all">' + esc(t("all", "All")) +
			'<span class="k-chip-count">' + rows.length + "</span></button>";
		statuses.forEach(function (s) {
			var n = rows.filter(function (r) { return (r.status || r.state || "") === s; }).length;
			html +=
				'<button type="button" class="k-chip' + (filter === s ? " active" : "") +
				'" data-filter="' + esc(s) + '">' + esc(s) +
				'<span class="k-chip-count">' + n + "</span></button>";
		});
		if (view.group) {
			html +=
				'<button type="button" class="k-group-toggle' + (state.listGroup ? " active" : "") +
				'" data-group-toggle="1" title="' +
				esc(state.listGroup ? t("group_by", "Grouping") : t("ungroup", "Toggle grouping")) + '">' +
				'<i class="fa ' + (state.listGroup ? "fa-layer-group" : "fa-bars") + '"></i> ' +
				esc(state.listGroup ? t("grouped", "Grouped") : t("list", "List")) +
				"</button>";
		}
		html +=
			'<div class="k-search"><i class="fa fa-search"></i><input type="search" id="panelSearch" value="' +
			esc(rawSearch) + '" placeholder="' + esc(t("search", "Search")) + '"></div>' +
			'<span class="text-muted small ms-auto">' + visible.length + " / " + rows.length + "</span>" +
			"</div>";

		if (view.note) {
			html += '<div class="k-alert k-alert-info"><i class="fa fa-info-circle"></i><span>' + esc(view.note) + "</span></div>";
		}

		if (!visible.length) {
			html += emptyBlock("fa-filter", t("no_match", "No records match your filters."), "");
		} else if (view.group && state.listGroup) {
			html += groupRows(visible, view);
		} else {
			html += visible.map(function (r) { return rowHtml(r, view); }).join("");
		}

		return html;
	}

	/* Odoo's group head: a coloured icon chip, the group label, and a count
	 * badge, followed by that group's rows. */
	function groupRows(rows, view) {
		var order = [];
		var buckets = {};
		rows.forEach(function (r) {
			var key = view.group.key(r);
			if (!buckets[key]) {
				buckets[key] = [];
				order.push(key);
			}
			buckets[key].push(r);
		});
		return order
			.map(function (key) {
				var items = buckets[key];
				return (
					'<div class="k-group-head">' +
					'<span class="d-flex align-items-center">' +
					'<span class="k-group-icon ' + view.group.variant + '"><i class="fa ' +
					view.group.icon + '"></i></span>' +
					'<span class="fw-bold">' + esc(key) + "</span></span>" +
					'<span class="k-soft-badge">' + items.length + "</span></div>" +
					items.map(function (r) { return rowHtml(r, view); }).join("")
				);
			})
			.join("");
	}

	function rowHtml(r, view) {
		var status = r.status || r.state || "";
		var meta = view.meta(r) || [];
		return (
			'<div class="k-row ' + rowTone(status) + '" data-row="' + esc(JSON.stringify(r)) + '">' +
			'<div class="k-row-head">' +
			'<span class="k-row-icon"><i class="fa ' + esc(view.icon) + '"></i></span>' +
			'<span class="flex-grow-1" style="min-width:0">' +
			'<span class="k-row-title d-block text-truncate">' + esc(view.title(r)) + "</span>" +
			(meta.length
				? '<span class="k-row-meta d-block">' +
				  meta.map(function (m) {
						return '<span><i class="fa ' + esc(m.icon) + '"></i> ' + esc(m.text) + "</span>";
				  }).join("") +
				  "</span>"
				: "") +
			"</span>" +
			(status ? '<span class="k-soft-badge">' + esc(status) + "</span>" : "") +
			'<button type="button" class="k-pill-btn k-row-action" data-row-action="view" title="' +
			esc(t("view", "View")) + '"><i class="fa fa-eye"></i></button>' +
			"</div></div>"
		);
	}

	/* the searchable text of a row, used by the search box */
	function rowText(r) {
		return Object.keys(r)
			.filter(function (k) {
				return k !== "name" && typeof r[k] !== "object";
			})
			.map(function (k) {
				return r[k];
			})
			.join(" ");
	}

	function formatMoney(value) {
		return Number(value || 0).toLocaleString(intlLocale(), {
			minimumFractionDigits: 2,
			maximumFractionDigits: 2
		});
	}

	function bindPanelEvents(action, data) {
		var body = document.getElementById("panelBody");
		if (!body) return;

		/* re-render rather than hide/show in place, so the count in the filter bar
		 * stays truthful as the filter narrows the list */
		function repaint() {
			body.innerHTML = renderList(action, data);
			bindPanelEvents(action, data);
		}

		var search = document.getElementById("panelSearch");
		if (search) {
			search.addEventListener("input", function (e) {
				state.listQuery = e.target.value;
				repaint();
				var s = document.getElementById("panelSearch");
				if (s) {
					s.focus();
					s.setSelectionRange(s.value.length, s.value.length);
				}
			});
		}

		Array.prototype.forEach.call(body.querySelectorAll("[data-filter]"), function (el) {
			bind(el, "click", function () {
				state.listFilter = el.dataset.filter;
				repaint();
			});
		});

		Array.prototype.forEach.call(body.querySelectorAll("[data-group-toggle]"), function (el) {
			bind(el, "click", function () {
				state.listGroup = !state.listGroup;
				repaint();
			});
		});

		Array.prototype.forEach.call(body.querySelectorAll("[data-row-action]"), function (el) {
			bind(el, "click", function () {
				var row = el.closest("[data-row]");
				if (!row) return;
				openRowDetail(action, JSON.parse(row.dataset.row));
			});
		});
	}

	/* Tapping a row's eye opens that record inline. Only the payslip screen has a
	 * detail endpoint today; the leave screens show the record's own fields,
	 * which the row already lists. */
	function openRowDetail(action, row) {
		var body = document.getElementById("panelBody");
		if (!body) return;

		if (action !== "payslips" || !row.name) {
			return;
		}
		state.loading = true;
		body.innerHTML = '<div class="text-center py-4 text-muted"><span class="k-spin"></span></div>';
		call({ action: "payslip", token: state.token, name: row.name })
			.then(function (d) {
				state.loading = false;
				body.innerHTML = renderPayslipDetail(d);
				bind(document.getElementById("detailBack"), "click", function () {
					showScreen("payslips");
				});
			})
			.catch(function (e) {
				state.loading = false;
				body.innerHTML =
					'<div class="k-alert k-alert-danger"><i class="fa fa-exclamation-triangle"></i><span>' +
					esc(e.message) + "</span></div>" +
					'<button type="button" class="k-pill-btn" id="detailBack"><i class="fa fa-arrow-left"></i> ' +
					esc(t("back", "Back")) + "</button>";
				bind(document.getElementById("detailBack"), "click", function () {
					showScreen("payslips");
				});
			});
	}

	function renderPayslipDetail(d) {
		function table(heading, icon, rows) {
			if (!rows || !rows.length) return "";
			return (
				'<div class="k-recap mb-3"><div class="fw-bold text-uppercase k-tracking mb-2" style="font-size:.72rem">' +
				'<i class="fa ' + icon + '"></i> ' + esc(heading) + "</div>" +
				rows.map(function (r) {
					return (
						'<div class="d-flex justify-content-between" style="font-size:.85rem;padding:.15rem 0">' +
						'<span>' + esc(r.name || r.salary_component || "") + "</span>" +
						'<span class="k-tabular">' + esc(formatMoney(r.amount)) + "</span></div>"
					);
				}).join("") +
				"</div>"
			);
		}
		return (
			'<button type="button" class="k-back mb-2" id="detailBack"><i class="fa fa-arrow-left"></i> ' +
			esc(t("back", "Back")) + "</button>" +
			'<div class="k-card"><span class="k-card-accent"></span><div class="k-card-body">' +
			'<h2 class="fw-bold mb-3" style="font-size:1.15rem">' + esc(t("payslip", "Payslip")) + "</h2>" +
			table(t("earnings", "Earnings"), "fa-arrow-circle-up", d.earnings) +
			table(t("deductions", "Deductions"), "fa-arrow-circle-down", d.deductions) +
			'<div class="k-recap mb-3" style="border-inline-start-color:#059669">' +
			'<div class="d-flex justify-content-between"><strong>' + esc(t("net_pay", "Net Pay")) + "</strong>" +
			'<span class="k-tabular fw-bold">' + esc(formatMoney(d.net_pay)) + " " + esc(d.currency || "") + "</span></div></div>" +
			// Reads `d.employer`, which is what `self_service.api.payslip` returns - it is
			// HRMS's own `employer_contributions` table, so this is a real table and
			// not an empty one. The table() helper returns "" for an empty list,
			// which is the right answer for a slip with no employer contributions.
			table(t("employer_side", "Employer Side (not deducted from net pay)"), "fa-building", d.employer) +
			"</div></div>"
		);
	}

	/* ------------------------------------------------------- session/reset */

	function resetToLogin() {
		var token = state.token;
		state.token = null;
		state.employee = null;
		state.approvalCount = 0;
		state.biometricWaiting = false;
		if (token) {
			call({ action: "sign_out", token: token }).catch(function () {});
		}
		showScreen("login");
	}

	/* ------------------------------------------------------ language/clock */

	function initLangMenu() {
		var btn = document.getElementById("langBtn");
		var dd = document.getElementById("langDd");
		var caret = document.getElementById("langCaret");
		if (!btn || !dd) return;

		function close() {
			dd.hidden = true;
			btn.classList.remove("active");
			btn.setAttribute("aria-expanded", "false");
			if (caret) caret.classList.remove("open");
		}

		bind(btn, "click", function (e) {
			e.stopPropagation();
			var open = dd.hidden;
			dd.hidden = !open;
			btn.classList.toggle("active", open);
			btn.setAttribute("aria-expanded", open ? "true" : "false");
			if (caret) caret.classList.toggle("open", open);
		});
		document.addEventListener("click", function (e) {
			if (!dd.hidden && !document.getElementById("langWrap").contains(e.target)) close();
		});

		/* The active item is marked server-side (the template renders it from
		 * SELF_SERVICE_LANGUAGES), so there is nothing to repaint here. Only the click
		 * is wired up. */
		Array.prototype.forEach.call(dd.querySelectorAll(".k-lang-item"), function (el) {
			bind(el, "click", function () {
				var code = el.dataset.code;
				close();
				if (code && code !== state.lang) switchLang(code);
			});
		});
	}

	/* Odoo set the ``hr_kiosk_lang`` cookie and reloaded. The cookie keeps the
	 * choice on a bare /self-services-employees visit; the query parameter makes
	 * the reload itself unambiguous, and any other parameter is preserved rather
	 * than wiped. */
	function switchLang(code) {
		if (!code || code === state.lang) return;

		var expires = new Date();
		expires.setFullYear(expires.getFullYear() + 1);
		document.cookie =
			"employee_self_service_lang=" + encodeURIComponent(code) +
			"; path=/; expires=" + expires.toUTCString() + "; SameSite=Lax";

		var url = new URL(window.location.href);
		url.searchParams.set("lang", code);
		window.location.assign(url.toString());
	}

	/* -------------------------------------------------------------- theme ---
	 *
	 * The whole light/dark mechanism is two custom-property palettes and an
	 * attribute on <html>; there is no per-element dark rule anywhere. So
	 * switching themes is one attribute write - no reload, no re-render, and the
	 * screen the employee is on does not flicker.
	 *
	 * "system" is kept as the stored value rather than resolved on write, so the
	 * tablet keeps following its own setting until somebody pins it. That is why
	 * the media query has to be watched while it is the active choice - the only
	 * listener this registers.
	 */

	var THEME_COOKIE = "employee_self_service_theme";
	var THEME_QUERY = "(prefers-color-scheme: dark)";

	function prefersDark() {
		return !!(window.matchMedia && window.matchMedia(THEME_QUERY).matches);
	}

	function resolveTheme(choice) {
		if (choice === "light" || choice === "dark") return choice;
		return prefersDark() ? "dark" : "light";
	}

	function currentThemeChoice() {
		var parts = document.cookie ? document.cookie.split(";") : [];
		for (var i = 0; i < parts.length; i++) {
			var pair = parts[i].trim().split("=");
			if (decodeURIComponent(pair[0]) === THEME_COOKIE) {
				var value = decodeURIComponent(pair[1] || "");
				return value || "system";
			}
		}
		return "system";
	}

	function paintTheme(choice) {
		var resolved = resolveTheme(choice);
		document.documentElement.setAttribute("data-k-theme", resolved);
		var icon = document.getElementById("themeIcon");
		if (icon) icon.className = "fa " + (resolved === "dark" ? "fa-moon-o" : "fa-sun-o");
		markThemeChoice(choice);
	}

	/* The tick is rendered server-side, so it is moved here rather than
	 * re-rendered. The *selection* is what gets repainted, not the resolved
	 * theme: with "system" chosen the tablet can flip at sunset, and a picker
	 * still showing the sun would misreport which theme is on screen. */
	function markThemeChoice(choice) {
		Array.prototype.forEach.call(
			document.querySelectorAll("#themeDd .k-lang-item"),
			function (el) {
				var selected = el.dataset.theme === choice;
				el.classList.toggle("active", selected);
				var glyph = el.querySelector(".k-lang-flag i");
				if (glyph) {
					glyph.className =
						"fa " + (selected ? "fa-check" : glyph.dataset.icon || "fa-adjust");
				}
			}
		);
		var btn = document.getElementById("themeBtn");
		if (btn) btn.classList.toggle("active", choice !== "system");
	}

	function setTheme(choice) {
		if (choice !== "light" && choice !== "dark" && choice !== "system") return;
		var expires = new Date();
		expires.setFullYear(expires.getFullYear() + 1);
		document.cookie =
			THEME_COOKIE + "=" + encodeURIComponent(choice) +
			"; path=/; expires=" + expires.toUTCString() + "; SameSite=Lax";
		paintTheme(choice);
	}

	function initThemeMenu() {
		var wrap = document.getElementById("themeWrap");
		var btn = document.getElementById("themeBtn");
		var dd = document.getElementById("themeDd");
		if (!btn || !dd || !wrap) return;

		// each option's own glyph is remembered, because marking the selection
		// overwrites it with a tick
		Array.prototype.forEach.call(dd.querySelectorAll(".k-lang-item"), function (el) {
			var glyph = el.querySelector(".k-lang-flag i");
			if (glyph) glyph.dataset.icon = glyph.className.replace("fa ", "");
		});

		paintTheme(currentThemeChoice());

		function close() {
			dd.hidden = true;
			btn.classList.remove("active");
			btn.setAttribute("aria-expanded", "false");
		}

		bind(btn, "click", function (e) {
			e.stopPropagation();
			var open = dd.hidden;
			dd.hidden = !open;
			btn.classList.toggle("active", open);
			btn.setAttribute("aria-expanded", open ? "true" : "false");
		});
		document.addEventListener("click", function (e) {
			if (!dd.hidden && !wrap.contains(e.target)) close();
		});

		Array.prototype.forEach.call(dd.querySelectorAll(".k-lang-item"), function (el) {
			bind(el, "click", function () {
				close();
				setTheme(el.dataset.theme);
			});
		});

		/* While "system" is the choice, follow the tablet. `addEventListener` on
		 * the MediaQueryList rather than the deprecated `addListener`, and guarded
		 * because older Safari only has the latter. */
		if (window.matchMedia && window.matchMedia(THEME_QUERY).addEventListener) {
			window.matchMedia(THEME_QUERY).addEventListener("change", function () {
				if (currentThemeChoice() === "system") paintTheme("system");
			});
		}
	}

	function clockDate() {
		return new Date().toLocaleDateString(intlLocale(), {
			weekday: "short",
			day: "2-digit",
			month: "short",
			year: "numeric"
		});
	}

	function tickClock() {
		var timeEl = document.getElementById("clockTime");
		var dateEl = document.getElementById("clockDate");
		if (timeEl) {
			timeEl.textContent = new Date().toLocaleTimeString(intlLocale(), {
				hour: "2-digit",
				minute: "2-digit"
			});
		}
		if (dateEl) dateEl.textContent = clockDate();
	}

	/* ------------------------------------------------- inactivity handling */

	var inactivityTimer = null;

	function armInactivity() {
		if (inactivityTimer) clearTimeout(inactivityTimer);
		if (state.screen === "login") return;
		inactivityTimer = setTimeout(function () {
			resetToLogin();
		}, state.inactivityMs || 30000);
	}

	function initInactivity() {
		// Odoo's ACTIVITY_EVENTS
		["click", "mousedown", "keydown", "touchstart", "scroll"].forEach(function (evt) {
			document.addEventListener(evt, armInactivity, { passive: true });
		});
	}

	/* ------------------------------------------------------ error reporting */

	/* A wall-mounted self-service has no devtools, so an uncaught exception used to
	 * present as "nothing works" with no clue as to why. Anything that escapes
	 * is now shown on screen as well as logged. */
	function showFatal(title, detail) {
		if (!host) return;
		var box = document.getElementById("essFatal");
		if (!box) {
			box = document.createElement("div");
			box.id = "essFatal";
			box.className = "k-fatal";
			host.appendChild(box);
		}
		box.innerHTML =
			'<div class="k-alert k-alert-danger mb-0" role="alert">' +
			'<i class="fa fa-exclamation-triangle"></i><span><strong>' + esc(title) + "</strong><br>" +
			'<span style="font-size:.8em">' + esc(detail || "") + "</span></span>" +
			'<button type="button" class="k-fatal-x" title="' + esc(t("dismiss", "Dismiss")) +
			'"><i class="fa fa-times"></i></button></div>';
		var x = box.querySelector(".k-fatal-x");
		if (x) bind(x, "click", function () { box.remove(); });
	}

	function initErrorReporting() {
		window.addEventListener("error", function (e) {
			showFatal(t("js_error", "Self service error"), (e.message || "") + " (" + (e.filename || "").split("/").pop() + ":" + e.lineno + ")");
		});
		window.addEventListener("unhandledrejection", function (e) {
			var r = e.reason || {};
			showFatal(t("js_error", "Self service error"), r.message || String(r));
		});
	}

	/* ----------------------------------------------------------------- dom */

	function bind(el, evt, handler) {
		if (el) el.addEventListener(evt, handler);
	}

	function escKeyGuard(e) {
		// Escape leaves a panel without needing the back button on a wall tablet
		if (e.key === "Escape" && state.screen !== "login" && state.screen !== "greet") {
			showScreen("dashboard");
		}
	}

	function init() {
		initErrorReporting();
		initLangMenu();
		initThemeMenu();
		initInactivity();
		tickClock();
		setInterval(tickClock, 1000);
		document.addEventListener("keydown", escKeyGuard);
		render();
	}

	if (document.readyState === "loading") {
		document.addEventListener("DOMContentLoaded", init);
	} else {
		init();
	}
})();
