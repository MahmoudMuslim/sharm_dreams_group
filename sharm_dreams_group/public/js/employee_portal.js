// Copyright (c) 2026, Sharm Dreams Group and contributors
// For license information, see license.txt

// The employee portal client.
//
// One IIFE, no framework, no build step: the page is a template and this file, and the
// whole screen is re-rendered on every state change. That is a deliberate trade for a
// page with five screens and no state that outlives one of them; it is the wrong shape
// for anything larger.
//
// Every action it posts is listed in `portal/renderers.py::ACTIONS`, and the server
// refuses anything not in that map - so a typo here is a 400 with a named action rather
// than a silent nothing.

(function () {
	"use strict";

	var CFG = window.EMPLOYEE_PORTAL || {};
	var API = CFG.api || "/employee-portal-api";
	var LANG_API = CFG.langApi || "/employee-portal/setlang";
	var TOKEN_KEY = "employee_portal.token";
	var THEME_KEY = "employee_portal.theme";
	// The credential id of this tablet's fingerprint or face reader is a public value, and
	// it lives in `localStorage` beside the session it stands in for.
	var WEBAUTH_KEY = "employee_portal.credential";
	var REMEMBER_KEY = "employee_portal.remembered";
	// The one piece of personal data this tablet is allowed to hold, and the one choice
	// that puts it there: a sign-in ticked "Keep me signed in on this tablet" is a person
	// saying the device is theirs, which is the same declaration that puts the session
	// token on disk. Everything else - a shared tablet, an unticked sign-in, a tablet with
	// a finger enrolled - keeps nothing about who signed in. See `saveIdentity`.
	var IDENTITY_KEY = "employee_portal.identity";

	var host = document.getElementById("screenHost");
	var root = document.getElementById("epRoot");
	if (!host || !root) return;

	// ------------------------------------------------------------------ words
	// The page arrives in one of three languages, and the words for it come over as data:
	// `CFG.strings` is `{english: translated}`, read off `translations/<lang>.csv` by
	// `portal/locales.py` - the same file Frappe reads to translate the desk, so a screen
	// and a tablet cannot call the same thing two different things.
	//
	// Why the swap happens after the render rather than in the markup. The honest reason
	// is shape: every screen here is built by concatenating HTML, so the English is in the
	// middle of strings that also carry tags and ids, and wrapping a few hundred of them
	// would mean touching a few hundred places in a file that no build step, bundler or
	// test can execute. `localizeNode` walks what was rendered instead, and covers the whole
	// surface at once - including this template's own header, whose text never passes
	// through this file.
	//
	// The price is matching what a text node actually says. A node is translated when:
	//
	//   it is a key - "No payslips yet";
	//   it is a key with the figures put back - "3 days left" is the row "{0} days left",
	//   where one figure is one hole whether it is written 1,200.50, 09:55 or 2026-11-01;
	//   it is several sayings joined by "·" and each of them passes one of the two tests
	//   above - "20 allocated · 5 taken" is two rows, not one;
	//   or a key heads it and what follows is data - "Reports to Amina Hassan".
	//
	// Anything else stays English, so a row is authored against the shape on the screen.
	// Two shapes can never be reached and are said with `t()` at the place they are built
	// instead: a sentence that spans two separate text nodes, and a phrase with a literal
	// digit in it, since `4 / 5` and `3 days` are indistinguishable to the second test.
	//
	// An English tablet gets `{}`. Every lookup misses, the observer is never installed,
	// and this whole section costs nothing and changes nothing - which matters because
	// that is the state most of these devices ship in.

	var STRINGS = CFG.strings || {};

	function t(key, values) {
		var out = STRINGS[key] || key;
		for (var i = 0; values && i < values.length; i++) {
			out = out.split("{" + i + "}").join(values[i]);
		}
		return out;
	}

	// The attributes whose value is a label a person reads or a screen reader says.
	// `value` is not one of them, and `TEXTAREA` is skipped below: what someone typed as
	// the reason for their own leave is their words, not the portal's.
	var L10N_ATTRS = ["placeholder", "aria-label", "title", "alt"];

	/* One number is one hole in a sentence, however it is written.
	 *
	 * `1,200.50`, `09:55`, `2026-11-01`, `7.5` - a machine reads those as one value and so
	 * does the person, and a row like `Joined {0}` has to be fillable by the join date for
	 * the catalog to be writable at all. Matching runs of digits instead would turn
	 * `Joined 2026-11-01` into `Joined {0}-{1}-{2}` and nothing would ever match it again.
	 *
	 * A separator only joins two groups when digits follow it, which is what keeps the full
	 * stop at the end of `Recorded 12.` from being eaten along with the number.
	 */
	var GROUP = /[0-9]+(?:[.,][0-9]+)*(?:[:\/-][0-9]+(?:[.,][0-9]+)*)*/g;

	function patternOf(text) {
		var i = 0;
		return text.replace(GROUP, function () {
			return "{" + i++ + "}";
		});
	}

	// The character these screens glue phrases together with, and the only one worth
	// splitting on: `2 days · approved by Amina` is two sayings, not one.
	var SEPARATOR = "\u00b7";

	/** `text` with every `·`-separated phrase translated, or null if none of them was. */
	function swapParts(text) {
		if (text.indexOf(SEPARATOR) < 0) return null;
		var pieces = text.split(SEPARATOR);
		var out = [];
		var changed = false;
		for (var i = 0; i < pieces.length; i++) {
			var piece = pieces[i];
			// A phrase inside the line is translated on its own, but never re-split, so
			// this cannot descend into itself.
			var swapped = swap(piece, true);
			if (swapped !== null) {
				out.push(swapped);
				changed = true;
			} else {
				out.push(piece);
			}
		}
		return changed ? out.join(SEPARATOR) : null;
	}

	/* The keys that can stand at the head of a longer line, longest first.
	 *
	 * "Reports to Amina", "Good morning, Sami", "Insurance 2026-11-01" -
	 * the label is one piece and whatever follows it is data, so no key is ever the whole
	 * node and the tiers above miss it. Rather than edit every place that concatenates
	 * a label with a value, a row may be used as a prefix - under three restrictions, each
	 * of which exists to stop a *sentence* being mistaken for a label:
	 *
	 *   the key has a space in it, so it is at least two words ("Approved" cannot eat the
	 *   middle of "Approved by payroll on Monday") - unless what follows it is only a
	 *   number, which is the shape of "Value 1,200.00" and cannot begin a sentence anyway;
	 *   it has no `{0}`, which is the other tier's business;
	 *   the node continues with a space or a comma, never a full stop - so "Cannot reach
	 *   the server" cannot bite into "Cannot reach the server. Check the network".
	 */
	var prefixKeys = null;

	// What may legally follow a one-word label: a figure, a date, a time.
	var TAIL = /^[0-9.,:\s\/\u2013-]+$/;

	function prefixFor(text) {
		if (prefixKeys === null) {
			prefixKeys = Object.keys(STRINGS).filter(function (key) {
				// `{0}` rows are the pattern tier's business, and a row carrying a `·` is a
				// whole line of phrases, which the tier above reassembles piece by piece.
				return key.indexOf("{") < 0 && key.indexOf(SEPARATOR) < 0;
			});
			// Longest first: "Leave requests you are named on" before "Leave balance".
			prefixKeys.sort(function (a, b) {
				return b.length - a.length;
			});
		}
		for (var i = 0; i < prefixKeys.length; i++) {
			var key = prefixKeys[i];
			var next = text.charAt(key.length);
			if ((next !== " " && next !== ",") || text.slice(0, key.length) !== key) continue;
			var rest = text.slice(key.length + 1);
			if (!rest) continue;
			if (key.indexOf(" ") < 0 && !TAIL.test(rest)) continue;
			return { key: key, rest: rest, joiner: next };
		}
		return null;
	}

	/** `text` translated, or null when no row fits. */
	function swap(text, isPiece) {
		var trimmed = (text || "").trim();
		if (!trimmed) return null;
		var hit = STRINGS[trimmed];
		var digits = null;
		if (!hit) {
			hit = STRINGS[patternOf(trimmed)];
			if (hit) {
				digits = trimmed.match(GROUP) || [];
				for (var i = 0; i < digits.length; i++) {
					hit = hit.split("{" + i + "}").join(digits[i]);
				}
			}
		}
		if (!hit && !isPiece) hit = swapParts(trimmed);
		if (!hit) {
			var prefix = prefixFor(trimmed);
			if (!prefix) return null;
			hit = STRINGS[prefix.key] + prefix.joiner + prefix.rest;
		}
		if (hit === trimmed) return null;
		// The padding goes back over the word: concatenated HTML leans on those spaces,
		// and dropping them would run "3" into "days" on the way past.
		return (/^\s*/.exec(text)[0] || "") + hit + (/\s*$/.exec(text)[0] || "");
	}

	function localizeNode(node) {
		if (node.nodeType === 3) {
			var swapped = swap(node.nodeValue);
			if (swapped !== null) node.nodeValue = swapped;
			return;
		}
		if (node.nodeType !== 1) return;
		var name = node.nodeName;
		if (name === "SCRIPT" || name === "STYLE" || name === "TEXTAREA") return;
		for (var a = 0; a < L10N_ATTRS.length; a++) {
			var attr = L10N_ATTRS[a];
			if (!node.hasAttribute(attr)) continue;
			var current = node.getAttribute(attr);
			var next = swap(current);
			if (next !== null && next !== current) node.setAttribute(attr, next);
		}
		for (var child = node.firstChild; child; child = child.nextSibling) {
			localizeNode(child);
		}
	}

	/* Keeping the language on the screen as the screen changes.
	 *
	 * Every panel here is repainted by assigning `innerHTML` somewhere in a file with more
	 * than a dozen of those assignments and a count that grows with each new screen, so
	 * watching the document beats editing each one: a later screen that forgets the call
	 * would show English on an Arabic tablet and nothing would say so.
	 *
	 * `localizing` stops the obvious recursion - a swap is itself a mutation the observer
	 * would otherwise be told about. It does not stop the asynchronous half of that: a
	 * record for our own write reaches the callback after the flag is back to false, so
	 * that pass runs `swap` over text that is already translated. It misses - a key is
	 * English and the text is not - and writes nothing, so the second pass is one and not
	 * a conversation. A row that translates a word to another word that is *also* a key is
	 * the one way to make that loop turn, and `swap` returning null for an unchanged hit is
	 * what stops even that from running away.
	 */
	var localizing = false;

	function watchLanguage() {
		if (!Object.keys(STRINGS).length) return;
		localizing = true;
		try {
			// The template's own markup - the header, the language menu, the offline
			// banner - is already on screen by now and never gets redrawn, so it is done
			// here once rather than waiting for a mutation that will not come.
			localizeNode(root);
		} finally {
			localizing = false;
		}
		if (!window.MutationObserver) return;
		new MutationObserver(function (records) {
			if (localizing) return;
			localizing = true;
			try {
				records.forEach(function (record) {
					if (record.type === "childList") {
						Array.prototype.forEach.call(record.addedNodes, localizeNode);
					} else {
						localizeNode(record.target);
					}
				});
			} finally {
				localizing = false;
			}
		}).observe(root, {
			childList: true,
			subtree: true,
			characterData: true,
			attributes: true,
			attributeFilter: L10N_ATTRS,
		});
	}

	// ------------------------------------------------------------------ state
	var state = {
		screen: "login",
		token: null,
		employee: null,
		loading: false,
		error: "",
		notifCounts: {},
		// Whether this session was asked to outlive the tab, and whether this tablet has
		// a biometric reader standing in front of it. Both read from storage at boot.
		remembered: false,
		credentialId: null,
		// A rejected attempt, kept for the redraw the rejection causes. Nothing is written
		// to storage here, and a sign-in that worked clears it: the card redraws with the
		// numbers that were typed so a mistyped digit is one edit rather than a re-entry of
		// fourteen.
		typed: null,
		// Set once, when a sign-in was remembered on a tablet with no biometric yet:
		// the offer belongs to the moment after that choice, not to every later visit.
		suggestBiometric: false,
		// Whether this browser will give us the tablet's own reader at all.
		bioReady: false,
		// The colleague directory once read. Cleared on sign out: it is a company's
		// staff list, and whoever picks up the tablet next may work elsewhere.
		directory: null,
	};

	/* Biometric sign-in, and what it actually protects.
	 *
	 * The reader answers a challenge the server issued, and the server checks the
	 * signature against the key it stored: `portal/biometrics.py`. The credential is on
	 * the employee's record, so HR can see it and revoke it, and what this browser keeps
	 * is only its public id - no session lives on the device that a touch did not buy.
	 *
	 * Two ways to use it. A tablet enrolled by one person unlocks with a single tap,
	 * numbers and all. A tablet shared down a floor is different: each person types their
	 * own numbers and then touches, so the key in the reader is theirs rather than the
	 * first colleague who got there - which is what the retired self-service page did, and
	 * is why this route takes the two fields as arguments.
	 *
	 * The way back is always the two numbers. A reader that fails, a finger that is wet,
	 * a browser that changes its mind - none of them can lock a person out of their own
	 * record, so nothing here ever refuses the ordinary sign-in.
	 */
	function fromBase64Url(value) {
		var padded = String(value).replace(/-/g, "+").replace(/_/g, "/");
		while (padded.length % 4) padded += "=";
		var binary = window.atob(padded);
		var bytes = new Uint8Array(binary.length);
		for (var i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
		return bytes;
	}

	// Two shapes arrive here. `attestationObject`, `authenticatorData` and `signature`
	// are ArrayBuffers; `clientDataJSON` is a ByteString - a plain JS string in which
	// each character already *is* one byte. Feeding the string to `new Uint8Array(...)`
	// is the trap in that difference: a string is not an object, so the constructor
	// takes the length path, reads it as NaN and hands back an empty array. The base64
	// of that is "", which the server decodes to nothing and refuses as a clientData
	// mismatch - so an enrolment would fail on the one field that binds it to this
	// challenge, with nothing in the message to say which field.
	function toBase64Url(value) {
		var binary = "";
		if (typeof value === "string") {
			binary = value;
		} else {
			var bytes = new Uint8Array(value);
			for (var i = 0; i < bytes.length; i++) binary += String.fromCharCode(bytes[i]);
		}
		return window.btoa(binary).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
	}

	// The options come back as JSON, and JSON has no byte type: the fields the browser
	// wants as `BufferSource` are base64url text until this turns them round. Naming
	// them here rather than discovering them at runtime is also the documentation -
	// `challenge`, `user.id` and the credential ids are the whole list, and a fourth
	// would fail silently as a wrong-type error from inside `credentials.create`.
	function browserOptions(options) {
		var pk = JSON.parse(JSON.stringify(options || {}));
		if (pk.challenge) pk.challenge = fromBase64Url(pk.challenge);
		if (pk.user && pk.user.id) pk.user.id = fromBase64Url(pk.user.id);
		(pk.allowCredentials || []).forEach(function (item) {
			item.id = fromBase64Url(item.id);
		});
		(pk.excludeCredentials || []).forEach(function (item) {
			item.id = fromBase64Url(item.id);
		});
		return pk;
	}

	// The answer, in the base64url text the server's verifier expects. Only the fields
	// this ceremony actually filled are sent: the API refuses an argument the action does
	// not name, so an empty `signature` riding along with an enrolment would be refused
	// for the wrong reason - "Unexpected argument" instead of anything about the key.
	function credentialAnswer(credential) {
		// `response` is a property on the credential, not a method: reading it the other
		// way threw here, after the reader had already been touched and the key already
		// made, so the ceremony looked like it worked on the device and then never
		// reached the endpoint that stores it.
		var response = credential.response;
		var answer = {
			credential_id: credential.id,
			client_data_json: toBase64Url(response.clientDataJSON),
		};
		if (response.attestationObject) answer.attestation_object = toBase64Url(response.attestationObject);
		if (response.authenticatorData) answer.authenticator_data = toBase64Url(response.authenticatorData);
		if (response.signature) answer.signature = toBase64Url(response.signature);
		return answer;
	}

	// Fingerprint reader, face camera, or the device PIN standing in for either. An
	// external security key would satisfy this too, and no tablet here carries one.
	function platformAuthenticator() {
		if (!window.PublicKeyCredential || !navigator.credentials || !window.isSecureContext) {
			return Promise.resolve(false);
		}
		if (!PublicKeyCredential.isUserVerifyingPlatformAuthenticatorAvailable) {
			return Promise.resolve(false);
		}
		return PublicKeyCredential.isUserVerifyingPlatformAuthenticatorAvailable().catch(
			function () {
				return false;
			}
		);
	}

	function storeCredential(id) {
		state.credentialId = id;
		// The key replaces the numbers as the way in, so the numbers go back out - same
		// rule as the session, and for the same reason: a tablet with a reader in front of
		// it should hold one door, not three.
		forgetIdentity();
		try {
			window.localStorage.setItem(WEBAUTH_KEY, id);
		} catch (e) {
			/* the session still works; it simply will not survive a reboot */
		}
	}

	function dropCredential() {
		state.credentialId = null;
		try {
			window.localStorage.removeItem(WEBAUTH_KEY);
		} catch (e) {
			/* nothing stored */
		}
	}

	// Handing the two steps of a ceremony round the API. `options` is what the server
	// issued, `verify` is the action that checks the browser's answer against it, and
	// `extra` carries whatever else that step needs - the session token for an enrolment,
	// nothing at all for an unlock.
	function webauthnCeremony(options, create, verify, extra) {
		var publicKey = browserOptions(options.public_key);
		var step = create ? navigator.credentials.create : navigator.credentials.get;
		return Promise.resolve(step.call(navigator.credentials, { publicKey: publicKey }))
			.then(function (credential) {
				if (!credential || !credential.id) throw new Error("No key came back from this device.");
				return call(
					Object.assign({ action: verify, preauth_token: options.preauth_token }, credentialAnswer(credential), extra || {})
				);
			});
	}

	// Enrolment is signed-in work: the key is stored against whoever is already in, and
	// `biometric_enroll_verify` refuses a challenge issued to a different employee.
	function enrollBiometric() {
		return call({ action: "biometric_enroll_options", token: state.token })
			.then(function (options) {
				return webauthnCeremony(options, true, "biometric_enroll_verify", {
					mode: options.mode,
					token: state.token,
				});
			})
			.then(function (answer) {
				storeCredential(answer.credential_id);
				return answer;
			});
	}

	// This tablet's own key, with no numbers typed: the stored credential id tells the
	// server whose employee record to ask about, and the assertion is what proves that it
	// was this person's finger rather than this person's tablet.
	function unlockWithBiometric() {
		if (!state.credentialId) {
			return Promise.reject(new Error("This tablet has no fingerprint or face set up yet."));
		}
		return call({ action: "biometric_unlock_options", credential_id: state.credentialId })
			.then(function (options) {
				// `remember_me` is deliberately absent: a row that outlived the tab would be
				// a session with no touch in front of it, and the touch is the point.
				return webauthnCeremony(options, false, "biometric_unlock_verify", {});
			})
			.then(finishTouchSignIn);
	}

	// Numbers, then a touch. First time on a tablet this enrols it; after that the same
	// two steps ask for the key instead of storing one, which is why the mode is read out
	// of the server's answer rather than guessed from what this device remembers.
	function touchSignIn(nationalId, employeeCode) {
		return call({
			action: "biometric_login_options",
			national_id: nationalId,
			employee_code: employeeCode,
		})
			.then(function (options) {
				return webauthnCeremony(options, options.mode === "register", "biometric_login_verify", {
					mode: options.mode,
				});
			})
			.then(function (answer) {
				if (answer.credential_id) storeCredential(answer.credential_id);
				return finishTouchSignIn(answer);
			});
	}

	// Two ways to finish, because the two kinds of door protect different things. The
	// numbers are the only door with no reader behind it, so that one saves a session;
	// every touch mints a fresh one and writes nothing to the device - including a session
	// an earlier visit left remembered, which would otherwise outlive the enrolment that
	// was supposed to stand in front of it. Both arrive through `enterPortal`.
	function finishSignIn(message, remembered, identity) {
		rememberToken(message.token, remembered, identity);
		// The offer belongs to the moment after a remembered sign-in on a tablet that has
		// no key yet, which is the one case where a fingerprint would have been useful.
		if (remembered) state.suggestBiometric = !state.credentialId;
		return openPortal(message);
	}

	function finishTouchSignIn(message) {
		clearRemembered();
		forgetIdentity();
		state.token = message.token;
		state.suggestBiometric = false;
		return openPortal(message);
	}

	// ------------------------------------------------------------- navigation
	/* One table for every screen, and one for the sections. The dock, the sidebar, the
	   heading above each screen and the badges on all three are read out of these, so
	   adding a screen is adding a row here - there is no second list somewhere else
	   that can be forgotten and go stale.
	   `hidden` screens are reached from inside a section - a form behind a list - and
	   are named in the heading but not offered in the sidebar. `back` is where the back
	   control on a narrow screen returns to. */
	var SECTIONS = [
		{ key: "home", label: "Overview", icon: "fa-th-large" },
		{ key: "timeoff", label: "Time off", icon: "fa-calendar-check-o" },
		{ key: "pay", label: "Pay", icon: "fa-money" },
		{ key: "time", label: "Time", icon: "fa-clock-o" },
		{ key: "growth", label: "Results", icon: "fa-line-chart" },
		{ key: "people", label: "People", icon: "fa-users" },
		{ key: "equipment", label: "Assets", icon: "fa-laptop" },
	];

	var SCREENS = {
		dashboard: { label: "Overview", section: "home", icon: "fa-th-large" },
		details: { label: "My details", section: "home", icon: "fa-id-badge" },
		balances: { label: "Leave balances", section: "timeoff", icon: "fa-balance-scale" },
		time_off: { label: "My requests", section: "timeoff", icon: "fa-calendar-check-o" },
		time_off_form: { label: "Request time off", section: "timeoff", icon: "fa-plus", hidden: true, back: "time_off" },
		// `approvers` is a gate rather than a label: the screen is only offered to people
		// whose user is named as a leave approver, which is the same test the server runs
		// before it answers `my_approvals` with anything.
		approvals: { label: "Approvals", section: "timeoff", icon: "fa-inbox", approvers: true },
		leave_calendar: { label: "Team calendar", section: "timeoff", icon: "fa-calendar" },
		payslips: { label: "My payslips", section: "pay", icon: "fa-file-pdf-o" },
		expenses: { label: "Expense claims", section: "pay", icon: "fa-money" },
		expense_form: { label: "New expense claim", section: "pay", icon: "fa-plus", hidden: true, back: "expenses" },
		attendance: { label: "Shift & attendance", section: "time", icon: "fa-clock-o" },
		timesheets: { label: "Timesheets", section: "time", icon: "fa-list-alt" },
		timesheet_form: { label: "Log time", section: "time", icon: "fa-plus", hidden: true, back: "timesheets" },
		overtime: { label: "Overtime", section: "time", icon: "fa-bolt" },
		overtime_form: { label: "Log overtime", section: "time", icon: "fa-plus", hidden: true, back: "overtime" },
		performance: { label: "My performance", section: "growth", icon: "fa-line-chart" },
		colleagues: { label: "Colleagues", section: "people", icon: "fa-address-book-o" },
		org_chart: { label: "Org chart", section: "people", icon: "fa-sitemap" },
		assets: { label: "My assets", section: "equipment", icon: "fa-laptop" },
	};

	// `Object.keys` rather than the table's own order, so the sidebar order is set here
	// and matches the dashboard tiles it sits beside.
	var SECTION_ORDER = {
		home: ["dashboard", "details"],
		timeoff: ["balances", "time_off", "approvals", "leave_calendar"],
		pay: ["payslips", "expenses"],
		time: ["attendance", "timesheets", "overtime"],
		growth: ["performance"],
		people: ["colleagues", "org_chart"],
		equipment: ["assets"],
	};

	function screensInSection(key) {
		return (SECTION_ORDER[key] || []).filter(function (screen) {
			var s = SCREENS[screen];
			if (!s || s.hidden) return false;
			// A manager's list is not a screen everybody needs: offering a row of
			// "Approvals" that is empty for nine employees out of ten is noise, and the
			// tenth would still find it under the same count the dashboard badge names.
			if (s.approvers && !isApprover()) return false;
			return true;
		});
	}

	// Read off the profile rather than guessed from a tile: the server answers this from
	// the roles on the employee's user, so a manager added in the desk appears here on
	// their next sign-in without anything on the tablet having to learn about it.
	function isApprover() {
		return !!(state.employee && state.employee.isApprover);
	}

	function sectionOf(screen) {
		var s = SCREENS[screen];
		return (s && s.section) || "home";
	}

	function sectionLabel(key) {
		for (var i = 0; i < SECTIONS.length; i++) {
			if (SECTIONS[i].key === key) return SECTIONS[i].label;
		}
		return "Overview";
	}

	// How many outstanding items a screen has, from the notification counts the
	// dashboard read once and kept.
	function countFor(screen) {
		return state.notifCounts[screen] || 0;
	}

	function countSection(key) {
		return screensInSection(key).reduce(function (total, screen) {
			return total + countFor(screen);
		}, 0);
	}

	function initialsOf(name) {
		var words = String(name || "?").trim().split(/\s+/).filter(Boolean);
		var letters = words
			.slice(0, 2)
			.map(function (w) {
				return w.charAt(0);
			})
			.join("");
		return letters.toUpperCase() || "?";
	}

	function closest(node, selector) {
		while (node && node !== document.body) {
			if (node.matches && node.matches(selector)) return node;
			node = node.parentNode;
		}
		return null;
	}

	// ---------------------------------------------------------------- helpers
	function esc(value) {
		if (value === null || value === undefined) return "";
		return String(value)
			.replace(/&/g, "&amp;")
			.replace(/</g, "&lt;")
			.replace(/>/g, "&gt;")
			.replace(/"/g, "&quot;")
			.replace(/'/g, "&#39;");
	}

	function money(value, currency) {
		var n = Number(value || 0);
		var text = n.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
		return currency ? text + " " + currency : text;
	}

	function dateOnly(value) {
		if (!value) return "";
		return String(value).split(" ")[0].split("T")[0];
	}

	function announce(message) {
		var live = document.getElementById("epLive");
		if (live) live.textContent = message;
	}

	/* The floating pill desk calls a toast, for the ten moments whose only other
	   feedback was a line of text in a live region nobody looks at. Same contract as
	   `frappe.ui.toast`: five seconds, a close button, the type carried by the icon
	   rather than by the text's colour, and an error announced as an interruption
	   (`role="alert"`) where everything else waits its turn (`role="status"`).
	*/
	var TOAST_ICONS = {
		success: "fa-check-circle",
		info: "fa-info-circle",
		warning: "fa-exclamation-triangle",
		error: "fa-times-circle"
	};

	var TOAST_MS = 5000;

	function dismissToast(el) {
		if (!el || !el.parentNode) return;
		el.classList.add("ep-toast--out");
		// The exit animation is 200ms and the node is not in the document by then if
		// something else removed it first, so this is a timer rather than
		// `animationend` - which would never fire on a detached element.
		setTimeout(function () {
			if (el.parentNode) el.parentNode.removeChild(el);
		}, 220);
	}

	function toast(message, type) {
		var kind = TOAST_ICONS[type] ? type : "success";
		var host = document.getElementById("epToasts");
		if (!host) {
			host = document.createElement("div");
			host.id = "epToasts";
			host.className = "ep-toast-container";
			document.body.appendChild(host);
		}
		// Three is the most a tablet can hold before the corner they share covers the
		// screen behind them; the oldest goes without waiting out its timer.
		while (host.children.length > 2) host.removeChild(host.firstElementChild);

		var el = document.createElement("div");
		el.className = "ep-toast";
		el.setAttribute("data-type", kind);
		el.setAttribute("role", kind === "error" ? "alert" : "status");
		el.innerHTML =
			'<i class="fa ' + TOAST_ICONS[kind] + ' ep-toast__icon" aria-hidden="true"></i>' +
			'<div class="ep-toast__content"><div class="ep-toast__message">' + esc(message) + "</div></div>" +
			'<button type="button" class="ep-toast__close" data-dismiss aria-label="' +
			esc(t("Close")) + '">&times;</button>';
		host.appendChild(el);

		var timer = setTimeout(function () { dismissToast(el); }, TOAST_MS);
		el.addEventListener("click", function (event) {
			if (!closest(event.target, "[data-dismiss]")) return;
			clearTimeout(timer);
			dismissToast(el);
		});
	}

	function csrf() {
		// The token the server put in the page. Frappe does not set a `csrf_token`
		// cookie for website visitors, so reading the cookie - the obvious thing to
		// do - yields "" and every POST comes back as an HTML "Invalid Request".
		return CFG.csrf || "";
	}

	// A failure that means the session is gone sends the employee back to sign-in
	// rather than leaving a screen full of empties they cannot leave.
	function expired(message) {
		if (/expired or invalid|Token Expired|Invalid CSRF/i.test(message || "")) {
			forgetToken();
			state.employee = null;
			state.directory = null;
			state.notifCounts = {};
			show("login");
			return true;
		}
		return false;
	}

	/* The server's refusal, in a sentence an employee can read.
	 *
	 * "Unknown portal action: biometric_status" is the server saying that *this file* is
	 * ahead of it: `public/` is symlinked and live on the next load while the Python is
	 * whatever the running worker last imported, so a worker that has not restarted does
	 * not know an action that exists on disk. It is a developer's sentence about an
	 * internal name, so it goes to the console and the tablet is told what it means.
	 */
	function sayable(message) {
		var unknown = /^Unknown portal action: (\S+)/.exec(message || "");
		if (!unknown) return message;
		console.warn("Portal action this server does not have:", unknown[1]);
		return t("This tablet is asking the server something it does not recognise. Please try again in a moment.");
	}

	function call(payload) {
		state.loading = true;
		// Per call, because "did this answer come off the cache" is a fact about one
		// response: a shared flag would have the next live screen clear the banner that
		// this cached one raised.
		var servedOffline = false;
		return fetch(API, {
			method: "POST",
			credentials: "same-origin",
			headers: { "Content-Type": "application/json", "X-Frappe-CSRF-Token": csrf() },
			body: JSON.stringify(payload),
		})
			.then(function (res) {
				return res.text().then(function (text) {
					var data;
					try {
						data = JSON.parse(text);
					} catch (e) {
						throw new Error("The server sent something unreadable (HTTP " + res.status + ").");
					}
					if (data.exc_type || data.exc) {
						var msgs = data._server_messages;
						throw new Error(msgs ? String(msgs) : data.exc || "Request failed.");
					}
					if (data.offline) {
						// Answered from the cache, or nothing cached at all: the banner
						// says so, and the screen still shows whatever was cached.
						servedOffline = true;
						showOfflineBanner();
					}
					if (data.message && data.message.error) throw new Error(sayable(data.message.error));
					return data.message;
				});
			})
			.then(function (message) {
				state.loading = false;
				if (!servedOffline) clearOfflineBanner();
				return message;
			})
			.catch(function (e) {
				state.loading = false;
				if (e && e.name === "TypeError") {
					showOfflineBanner();
					throw new Error("Cannot reach the server. Check the network and try again.");
				}
				if (e && /offline/i.test(e.message || "")) showOfflineBanner();
				throw e;
			});
	}

	// The banner beside the offline element in the page shell. Shown when a request
	// could not be answered, not merely when the browser reports the network down -
	// a captive-proxy wifi says "online" and still refuses every call.
	function showOfflineBanner() {
		var banner = document.getElementById("epOfflineBanner");
		if (banner) banner.hidden = false;
		// The CSS shows the banner only under `.ep-root.ep-offline`, so both have to
		// move together - setting `hidden` alone leaves it invisible.
		root.classList.add("ep-offline");
	}

	// An answer that came back means the network came back: a banner left up after the
	// first successful call is a banner that says "offline" about data that is live.
	// Never about a cached one - the worker marks those, and only a request that went
	// out and returned clears the notice.
	function clearOfflineBanner() {
		if (!navigator.onLine) return;
		root.classList.remove("ep-offline");
		var banner = document.getElementById("epOfflineBanner");
		if (banner) banner.hidden = true;
	}

	function fail(error) {
		// Cleared before render, because a refusal is the only thing that sets it and
		// a stale one would greet the next screen.
		if (expired(error && error.message)) return;
		state.error = (error && error.message) || "Something went wrong.";
		render();
	}

	// Two stores under one key. An ordinary session belongs to the tab and dies with
	// it; a *remembered* one has to outlive a reboot, so it is written to the durable
	// store and the server is told at sign-in to give the row the longer expiry. What is
	// remembered is the session, and - because the tick that bought it is a claim that
	// this tablet belongs to the person holding it - the two numbers that opened it, so
	// the next visit starts filled in.
	function rememberToken(token, remembered, identity) {
		state.token = token;
		// A tablet that has been given a fingerprint does not also keep a session on
		// disk. The alternative is two doors on one lock: a token taken off the storage
		// would open the portal without the reader ever being asked, and nothing on the
		// tablet would say so. The touch is the remembering, here.
		state.remembered = !!remembered && !state.credentialId;
		try {
			if (state.remembered) {
				window.localStorage.setItem(TOKEN_KEY, token);
				window.localStorage.setItem(REMEMBER_KEY, "1");
				window.sessionStorage.removeItem(TOKEN_KEY);
				if (identity) saveIdentity(identity);
			} else {
				window.sessionStorage.setItem(TOKEN_KEY, token);
				clearRemembered();
				forgetIdentity();
			}
		} catch (e) {
			/* private browsing: the session simply will not survive a reload */
		}
	}

	// Outlives the session on purpose. A remembered sign-in expires after eight hours,
	// which is once a day, and the thing an employee is hunting for the morning after is
	// not the session - it is the fourteen digits. The numbers stay until the tablet is
	// told otherwise: an unticked sign-in, a finger enrolled or used, or "Not you?".
	function saveIdentity(identity) {
		var nationalId = String(identity.nationalId || "").trim().slice(0, 32);
		var employeeCode = String(identity.employeeCode || "").trim().slice(0, 32);
		if (!nationalId || !employeeCode) return;
		try {
			window.localStorage.setItem(IDENTITY_KEY, JSON.stringify({ national_id: nationalId, employee_code: employeeCode }));
		} catch (e) {
			/* the tablet keeps nothing, so the next sign-in is typed as it always was */
		}
	}

	// Read at render rather than at boot, because the sign-in that fills it happens in
	// this tab. A damaged or hand-edited row answers null rather than half a person.
	function restoreIdentity() {
		try {
			var saved = JSON.parse(window.localStorage.getItem(IDENTITY_KEY) || "null");
			if (!saved || !saved.national_id || !saved.employee_code) return null;
			return { nationalId: String(saved.national_id), employeeCode: String(saved.employee_code) };
		} catch (e) {
			return null;
		}
	}

	function forgetIdentity() {
		try {
			window.localStorage.removeItem(IDENTITY_KEY);
		} catch (e) {
			/* nothing stored */
		}
	}

	// Forgets the session, not the key. The credential this tablet was enrolled with is a
	// public identifier that survives signing out, an expired row and a reset, because
	// which finger the reader should ask for is a fact about the tablet rather than about
	// today's visit - and it opens nothing on its own.
	function clearRemembered() {
		state.remembered = false;
		try {
			window.localStorage.removeItem(TOKEN_KEY);
			window.localStorage.removeItem(REMEMBER_KEY);
		} catch (e) {
			/* nothing was stored, so there is nothing to take back out */
		}
	}

	function forgetToken() {
		state.token = null;
		state.employee = null;
		try {
			window.sessionStorage.removeItem(TOKEN_KEY);
		} catch (e) {
			/* nothing to clean up */
		}
		clearRemembered();
	}

	function restoreToken() {
		try {
			return (
				window.sessionStorage.getItem(TOKEN_KEY) ||
				window.localStorage.getItem(TOKEN_KEY)
			);
		} catch (e) {
			return null;
		}
	}

	// Read once, at boot. The two facts are about this tablet rather than about this
	// visit, and they are independent: a remembered session is a token on disk, and a
	// credential id is the address of a key on the server. Losing one says nothing about
	// the other - which is why expiring a session leaves the enrolment alone, and why an
	// enrolled tablet still offers the ordinary sign-in beside it.
	function restoreRemembered() {
		try {
			state.remembered = window.localStorage.getItem(REMEMBER_KEY) === "1";
			state.credentialId = window.localStorage.getItem(WEBAUTH_KEY) || null;
		} catch (e) {
			state.remembered = false;
			state.credentialId = null;
		}
	}

	// ---------------------------------------------------------------- screens
	function show(screen) {
		state.screen = screen;
		state.error = "";
		updateHeader();
		renderShell();
		// The drawer overlaps the body on the widths where it is a drawer; leaving it
		// open after a pick would hide the screen that was just chosen.
		setNavOpen(false);
		var main = document.querySelector(".ep-main");
		if (main) main.scrollTop = 0;
		var s = SCREENS[screen];
		if (s) announce(s.label);
		render();
		// The clock restarts with the screen. A listener on the document alone would
		// count the tap that got here and nothing after it, so a screen arrived at by a
		// fingerprint, or by the resume button, would never be timed at all.
		armInactivity();
	}

	// The chrome beside the body: the section rail and, for the selected section, the
	// screens in it. Re-rendered on every screen change, because what is highlighted
	// and how many items are outstanding both follow where the employee is.
	function renderShell() {
		var dock = document.getElementById("epDock");
		var nav = document.getElementById("epNav");
		var title = document.getElementById("epSidebarTitle");
		if (!dock || !nav || !title) return;

		if (state.screen === "login") {
			root.classList.add("ep-locked");
			dock.innerHTML = "";
			nav.innerHTML = "";
			return;
		}
		root.classList.remove("ep-locked");

		var active = sectionOf(state.screen);

		dock.innerHTML = SECTIONS.map(function (section) {
			var here = section.key === active;
			var count = countSection(section.key);
			return (
				'<button type="button" class="ep-dock-item" data-section="' +
				esc(section.key) +
				'"' +
				(here ? ' aria-current="page"' : "") +
				"><i class=\"fa " +
				esc(section.icon) +
				'"></i><span class="ep-dock-label">' +
				esc(section.label) +
				"</span>" +
				(count ? '<span class="ep-dock-dot" title="' + esc(count) + ' outstanding"></span>' : "") +
				"</button>"
			);
		}).join("");

		title.textContent = sectionLabel(active);

		// Overview is the one screen that is not "inside" a section, so its sidebar
		// lists the sections themselves - the same destinations as the rail, in words.
		// Your own record sits at the end of that list, because it is a destination too,
		// and the only one on this side of the portal that is not about time, pay or
		// other people.
		if (active === "home") {
			nav.innerHTML = SECTIONS.filter(function (section) {
				return section.key !== "home";
			})
				.map(function (section) {
					var first = screensInSection(section.key)[0];
					return first ? navItem(first, section.label, section.icon) : "";
				})
				.join("") + navItem("details", SCREENS.details.label, SCREENS.details.icon);
			return;
		}

		nav.innerHTML = screensInSection(active)
			.map(function (screen) {
				return navItem(screen, SCREENS[screen].label, SCREENS[screen].icon);
			})
			.join("");
	}

	function navItem(screen, label, icon) {
		var here = screen === state.screen;
		var count = countFor(screen);
		return (
			'<button type="button" role="listitem" class="ep-nav-item" data-screen="' +
			esc(screen) +
			'"' +
			(here ? ' aria-current="page"' : "") +
			"><i class=\"fa " +
			esc(icon) +
			'"></i><span>' +
			esc(label) +
			"</span>" +
			(count ? '<span class="ep-nav-count">' + esc(count > 9 ? "9+" : count) + "</span>" : "") +
			"</button>"
		);
	}

	function setNavOpen(open) {
		root.classList.toggle("ep-nav-open", !!open);
		var toggle = document.getElementById("epNavToggle");
		if (toggle) toggle.setAttribute("aria-expanded", open ? "true" : "false");
	}

	// One figure on a screen that has several: assets in service, days left, claims
	// outstanding. The same shape each time, so the eye knows where the number is.
	function stat(value, label) {
		return (
			'<div class="ep-stat"><div class="ep-stat-value">' +
			esc(value === null || value === undefined ? "-" : value) +
			'</div><div class="ep-stat-label">' +
			esc(label) +
			"</div></div>"
		);
	}

	// What the content about to arrive looks like, as flat blocks. A spinner says
	// "wait"; this says "it will be here, and about this tall".
	function loadingBlock(rows) {
		var out = "";
		for (var i = 0, n = rows || 4; i < n; i++) {
			out +=
				'<div class="ep-skeleton-row"><div class="ep-skeleton ep-skeleton--title"></div>' +
				'<div class="ep-skeleton ep-skeleton--short"></div></div>';
		}
		return out;
	}

	// The card that holds a panel body, with its id - the part that survives the
	// fetch - already in place around the skeleton that does not.
	function loadingPanel(cls) {
		return '<div class="' + esc(cls || "ep-card") + '" id="panelBody">' + loadingBlock(4) + "</div>";
	}

	// The one button a list screen offers, in the heading rather than floating over the
	// body: it is the screen's purpose, not a decoration beside it.
	function actionButton(id, icon, label) {
		return (
			'<button type="button" class="ep-btn ep-btn-ghost" id="' +
			esc(id) +
			'"><i class="fa ' +
			esc(icon) +
			'"></i> ' +
			esc(label) +
			"</button>"
		);
	}

	// The heading every screen opens with: where the employee is, and the way back on
	// the widths where the sidebar is hidden behind the drawer. `backId` is left to the
	// caller because each screen binds its own return - a form goes back to its list,
	// a list goes back to the overview.
	function screenBar(backId, action) {
		var s = SCREENS[state.screen] || {};
		var crumb = sectionLabel(s.section || "home");
		if (s.hidden && s.back && SCREENS[s.back]) {
			crumb = sectionLabel(s.section) + " · " + SCREENS[s.back].label;
		}
		return (
			'<div class="ep-screen-bar">' +
			'<button type="button" class="ep-back" id="' +
			esc(backId) +
			'" aria-label="Back"><i class="fa fa-arrow-left"></i></button>' +
			'<div class="ep-screen-heading">' +
			'<h1 class="ep-screen-title">' +
			esc(s.label || "Employee Portal") +
			'</h1><p class="ep-screen-sub">' +
			esc(crumb) +
			"</p></div>" +
			(action ? '<div class="ep-screen-actions">' + action + "</div>" : "") +
			"</div>"
		);
	}

	// The name beside the switchers in the header. Kept in the page rather than in a
	// screen, because who is signed in does not change when they move between screens.
	function updateHeader() {
		var wrap = document.getElementById("userInfo");
		var signedIn = !!state.token && state.screen !== "login";
		if (wrap) wrap.hidden = !signedIn;
		signOutControls().forEach(function (btn) { btn.hidden = !signedIn; });
		if (!wrap || !signedIn) return;

		var e = state.employee || {};
		var avatar = document.getElementById("userAvatar");
		var name = document.getElementById("userName");
		var role = document.getElementById("userRole");
		if (name) name.textContent = e.employee_name || "";
		if (role) {
			role.textContent = [e.designation, e.department, e.company].filter(Boolean).join(" · ");
		}
		if (avatar) {
			avatar.innerHTML = e.image
				? '<img src="' + esc(e.image) + '" alt="" />'
				: esc(initialsOf(e.employee_name));
		}
	}

	function initNav() {
		var dock = document.getElementById("epDock");
		var nav = document.getElementById("epNav");
		var toggle = document.getElementById("epNavToggle");
		var scrim = document.getElementById("epNavScrim");

		// Both containers outlive every render, so one listener each covers every
		// button that is later written into them.
		if (dock) {
			dock.addEventListener("click", function (e) {
				var btn = closest(e.target, "[data-section]");
				if (!btn) return;
				var first = screensInSection(btn.dataset.section)[0];
				if (first) show(first);
			});
		}
		if (nav) {
			nav.addEventListener("click", function (e) {
				var btn = closest(e.target, "[data-screen]");
				if (btn) show(btn.dataset.screen);
			});
		}
		if (toggle) {
			toggle.addEventListener("click", function () {
				setNavOpen(!root.classList.contains("ep-nav-open"));
			});
		}
		if (scrim) scrim.addEventListener("click", function () { setNavOpen(false); });
		document.addEventListener("keydown", function (e) {
			if (e.key === "Escape") setNavOpen(false);
		});
	}

	function render() {
		switch (state.screen) {
			case "dashboard":
				return renderDashboard();
			case "details":
				return renderDetails();
			case "balances":
				return renderBalances();
			case "time_off":
				return renderTimeOff();
			case "approvals":
				return renderPanel("my_approvals", "approvals", approvalsHtml);
			case "time_off_form":
				return renderTimeOffForm();
			case "colleagues":
				return renderColleagues();
			case "org_chart":
				return renderOrgChart();
			case "timesheets":
				return renderTimesheets();
			case "timesheet_form":
				return renderTimesheetForm();
			case "overtime":
				return renderOvertime();
			case "overtime_form":
				return renderOvertimeForm();
			case "leave_calendar":
				return renderLeaveCalendar();
			case "assets":
				return renderAssets();
			case "expenses":
				return renderPanel(
					"my_expense_claims", "claims", renderClaimList, bindClaimRows
				);
			case "expense_form":
				return renderExpenseForm();
			// No "expense_claim" screen: a claim's detail is always rendered over the
			// list by `openClaim`, which needs the claim's name.
			case "payslips":
				return renderPanel("my_payslips", "payslips", renderPayslipList);
			case "attendance":
				return renderAttendance();
			case "performance":
				return renderPanel(
					"my_performance", "performance", renderPerformance, bindPerformance
				);
			// No "payslip" screen: a slip is always opened through `openPayslip`, which
			// needs the slip's name. Routing it through `renderPanel` would post
			// `my_payslip` with no `name` and come back refused.
			default:
				return renderLogin();
		}
	}

	function renderLogin() {
		// One tap needs a key this tablet was enrolled with. The numbers-then-touch route
		// needs a reader and no key of its own, which is the tablet down the corridor that
		// eleven people sign in on: nobody stores their session there, but everybody can
		// use their own finger. Everything else is the two numbers.
		var oneTap = !!state.credentialId;
		var shared = state.bioReady && !oneTap;
		// What the inactivity lock hid was the screen, not the session: ticking "Keep me
		// signed in" was a promise not to type the numbers again, and taking it back over
		// idle time would break it. This is the promise kept - one tap, and the server
		// decides whether the session is still live rather than the tablet remembering that
		// it was. A tablet with a key in its reader has nothing here, because its way back
		// in is the finger.
		var saved = oneTap ? null : restoreToken();
		// What goes in the two fields before anyone types. A tablet that was told it
		// belongs to someone answers with the numbers of its last successful sign-in;
		// anything else - a shared tablet, a first visit, a session that was never
		// remembered - answers with the attempt that was refused, so a mistyped digit is
		// one edit rather than a re-entry of fourteen. Prefilled, never submitted: the tap
		// stays the employee's, because a card that signs you in on arrival is a session
		// that opens itself.
		var known = restoreIdentity() || state.typed;

		host.innerHTML =
			'<div class="ep-fade">' +
			'<div class="ep-card ep-login">' +
			'<h1 class="ep-login-title">Employee Portal</h1>' +
			(oneTap
				? '<p class="ep-login-sub">Welcome back. Unlock this tablet with your fingerprint or face.</p>' +
					'<button type="button" class="ep-btn ep-bio" id="bioBtn">' +
					'<i class="fa fa-fingerprint"></i> Sign in biometrically</button>' +
					'<div class="ep-or"><span>or with your numbers</span></div>'
				: '<p class="ep-login-sub">Sign in with your National ID and Employee Number.</p>') +
			(saved
				? '<button type="button" class="ep-btn ep-btn-ghost" id="resumeBtn">' +
					'<i class="fa fa-user-circle-o"></i> Resume this tablet&rsquo;s saved session</button>'
				: "") +
			alertHtml() +
			'<form id="loginForm" novalidate>' +
			'<div class="ep-field"><label class="ep-label" for="nid">National ID</label>' +
			'<input class="ep-input" id="nid" name="national_id" inputmode="numeric" autocomplete="off" required value="' +
			esc(known ? known.nationalId : "") +
			'" /></div>' +
			'<div class="ep-field"><label class="ep-label" for="code">Employee Number</label>' +
			'<input class="ep-input" id="code" name="employee_code" autocomplete="off" required value="' +
			esc(known ? known.employeeCode : "") +
			'" /></div>' +
			(known
				? '<button type="button" class="ep-btn ep-btn-ghost ep-btn-quiet" id="notYouBtn">' +
					'<i class="fa fa-user-times"></i> Not you? Clear these numbers</button>'
				: "") +
			'<div class="ep-field"><div class="ep-checkbox-field">' +
			'<input type="checkbox" id="rememberMe" name="remember_me" value="1"' +
			(state.remembered ? " checked" : "") +
			' /><label class="ep-checkbox-label" for="rememberMe">' +
			"Keep me signed in on this tablet</label></div>" +
			'<p class="ep-hint">Saves the session and keeps these two numbers here, so the next ' +
			"sign-in is one tap rather than two fields. Use it only on a tablet that is yours - " +
			'Not you? takes the numbers back out.</p></div>' +
			'<button class="ep-btn" type="submit" id="loginBtn">' +
			(state.loading ? '<span class="ep-spin"></span> Signing in' : "Sign in") +
			"</button>" +
			(shared
				? '<button class="ep-btn ep-btn-ghost" type="button" id="touchBtn">' +
					'<i class="fa fa-fingerprint"></i> Sign in with your fingerprint</button>' +
					'<p class="ep-hint">Type your numbers, then touch the reader. On a tablet several ' +
					"of you share this keeps each person's finger on their own sign-in, and the first " +
					"time through enrols the key.</p>"
				: "") +
			"</form>" +
			installHint() +
			"</div></div>";

		var form = document.getElementById("loginForm");
		form.addEventListener("submit", function (e) {
			e.preventDefault();
			// Read before the round trip, because `render()` below rebuilds this card and
			// the tick would be gone by the time the answer came back.
			var remembered = document.getElementById("rememberMe").checked;
			signIn(document.getElementById("nid").value, document.getElementById("code").value, remembered);
		});

		// The numbers only, not the session beside them: this is a person saying the next
		// hand on this tablet is not the one the numbers belong to, and the resume button is
		// that other person's way back in - it stays for them to decide about.
		var notYou = document.getElementById("notYouBtn");
		if (notYou) {
			notYou.addEventListener("click", function () {
				forgetIdentity();
				state.typed = null;
				render();
			});
		}

		var resume = document.getElementById("resumeBtn");
		if (resume) {
			resume.addEventListener("click", function () {
				busy(resume, "Checking");
				// Re-read rather than believed: the session on the disk is a claim about
				// the past, and only the server knows whether it is still one.
				restoreSession(saved);
			});
		}

		var touchBtn = document.getElementById("touchBtn");
		if (touchBtn) {
			touchBtn.addEventListener("click", function () {
				// The two numbers are what tells the server whose key to ask for, so the
				// reader is only worth waking once they are in.
				var nationalId = document.getElementById("nid").value;
				var code = document.getElementById("code").value;
				if (!String(nationalId).trim() || !String(code).trim()) {
					state.error = "Enter your National ID and Employee Number first.";
					render();
					return;
				}
				busy(touchBtn, "Touch the reader");
				touchSignIn(nationalId, code).catch(fail);
			});
		}

		var bioBtn = document.getElementById("bioBtn");
		if (bioBtn) {
			bioBtn.addEventListener("click", function () {
				busy(bioBtn, "Waiting for you");
				unlockWithBiometric().catch(fail);
			});
		}

		// An empty field is the one that wants the cursor. A card that arrived filled does
		// not: the focus goes to the action instead, which keeps the on-screen keyboard off
		// the tablet until someone actually decides to change a number.
		if (oneTap) return;
		var filled = known && String(known.nationalId).trim() && String(known.employeeCode).trim();
		document.getElementById(filled ? "loginBtn" : "nid").focus();
	}

	// A button held for the length of a round trip: the reader is waiting, so the tap must
	// not be repeatable. Nothing puts the label back by hand - the failure path renders the
	// card again, and a control restored here would be a second copy of markup the render
	// is about to write.
	function busy(button, label) {
		button.disabled = true;
		button.innerHTML = '<span class="ep-spin"></span> ' + label;
	}

	// Running as an installed app, on either engine that reports it.
	function isStandalone() {
		if (window.matchMedia && window.matchMedia("(display-mode: standalone)").matches) return true;
		return window.navigator.standalone === true;
	}

	// iPads never fire `beforeinstallprompt`, so the browser gives an in-page button no
	// way to press. The one route in is the Share sheet, and it has to be said in words.
	function installHint() {
		if (isStandalone()) return "";
		if (!/ipad|iphone|ipod/i.test(window.navigator.userAgent)) return "";
		return (
			'<p class="ep-hint"><i class="fa fa-mobile"></i> To put this on the Home ' +
			"Screen: press Share, then Add to Home Screen.</p>"
		);
	}

	function signIn(nationalId, employeeCode, remembered) {
		state.loading = true;
		// Kept through the re-render this call triggers, so the ticked box is still
		// ticked if the numbers turn out to be wrong and the card is drawn again.
		state.remembered = !!remembered;
		state.typed = { nationalId: nationalId, employeeCode: employeeCode };
		render();
		call({
			action: "sign_in",
			national_id: nationalId,
			employee_code: employeeCode,
			remember_me: !!remembered,
		})
			.then(function (message) {
				// Only a refusal is worth holding. The numbers that worked are kept by
				// `saveIdentity` if the tablet was told it belongs to someone, and by
				// nothing at all if it was not.
				state.typed = null;
				return finishSignIn(message, remembered, { nationalId: nationalId, employeeCode: employeeCode });
			})
			.catch(fail);
	}

	// Every door arrives through here - a restored session, a remembered one, a touch -
	// and it would be a strange portal whose entrances showed different screens.
	function enterPortal() {
		state.directory = null;
		show("dashboard");
		return call({ action: "my_profile", token: state.token })
			.then(function (p) {
				state.employee = p;
				updateHeader();
				// The navigation is drawn before this answer arrives, and one of its rows
				// only exists for a leave approver - so the rail is rebuilt here rather
				// than left to show a manager their own screen is missing until they tap
				// somewhere else.
				renderShell();
			})
			.catch(function (err) {
				if (expired(err && err.message)) return;
				fail(err);
			});
	}

	// `openPortal` is the entrance that also knows who is signing in: the sign-in reply
	// fills the header immediately, and the profile read then replaces it with the whole
	// record rather than the shorter shape.
	function openPortal(message) {
		state.employee = message || state.employee;
		return enterPortal();
	}

	function renderDashboard() {
		var e = state.employee || {};
		call({ action: "my_notifications", token: state.token })
			.then(function (d) {
				renderDashboardWithNotifications(e, d.items || []);
			})
			.catch(function () {
				renderDashboardWithNotifications(e, []);
			});
	}

	function renderDashboardWithNotifications(e, notifications) {
		state.notifCounts = countNotificationsByScreen(notifications);
		// After the counts, because the rail and the sidebar carry the same badges the
		// tiles do, and a stale rail beside fresh tiles reads as two different answers.
		renderShell();

		var groups = SECTIONS.filter(function (section) {
			return section.key !== "home";
		})
			.map(function (section) {
				var screens = screensInSection(section.key);
				if (!screens.length) return "";
				return (
					'<div class="ep-section-head"><h2>' +
					esc(section.label) +
					"</h2></div>" +
					'<div class="ep-tiles">' +
					screens
						.map(function (screen) {
							return tile(screen, SCREENS[screen].icon, SCREENS[screen].label, countFor(screen));
						})
						.join("") +
					"</div>"
				);
			})
			.join("");

		host.innerHTML =
			'<div class="ep-fade">' +
			'<div class="ep-card ep-hero">' +
			'<div class="ep-hero-avatar">' +
			(e.image
				? '<img src="' + esc(e.image) + '" alt="" />'
				: esc(initialsOf(e.employee_name))) +
			"</div>" +
			'<div class="ep-hero-text">' +
			'<h1 class="ep-hero-name">' +
			esc(greeting()) +
			", " +
			esc(String(e.employee_name || "").split(" ")[0] || "") +
			"</h1>" +
			'<p class="ep-hero-sub">' +
			esc([e.designation, e.department, e.company].filter(Boolean).join(" · ")) +
			"</p></div>" +
			(e.employee_number
				? '<div class="ep-hero-meta">' +
					esc(e.employee_number) +
					'<button type="button" class="ep-btn ep-btn-ghost" data-tile="details">' +
					'<i class="fa fa-id-badge"></i> My details</button></div>'
				: "") +
			"</div>" +
			alertHtml() +
			biometricOfferHtml() +
			(notifications.length ? renderNotificationStrip(notifications) : "") +
			groups +
			"</div>";
		bindTiles();
	}

	function greeting() {
		var hour = new Date().getHours();
		if (hour < 12) return "Good morning";
		if (hour < 17) return "Good afternoon";
		return "Good evening";
	}

	function countNotificationsByScreen(items) {
		var counts = { balances: 0, time_off: 0, expenses: 0, payslips: 0, attendance: 0, performance: 0, colleagues: 0, org_chart: 0, timesheets: 0, overtime: 0, leave_calendar: 0, assets: 0 };
		items.forEach(function (item) {
			if (counts.hasOwnProperty(item.screen)) {
				counts[item.screen]++;
			}
		});
		return counts;
	}

	function renderNotificationStrip(items) {
		var html = '<div class="ep-attn-strip">';
		items.forEach(function (item) {
			html +=
				'<button type="button" class="ep-attn ep-attn-icon--' +
				esc(item.tone) +
				'" data-screen="' +
				esc(item.screen) +
				'">' +
				'<span class="ep-attn-icon ep-attn-icon--' +
				esc(item.tone) +
				'"><i class="fa ' +
				esc(item.icon) +
				'"></i></span>' +
				'<div class="ep-attn-text">' +
				'<div class="ep-attn-head">' +
				esc(item.headline) +
				"</div>" +
				'<div class="ep-attn-detail">' +
				esc(item.detail) +
				"</div>" +
				"</div>" +
				"</button>";
		});
		html += "</div>";
		return html;
	}

	function tile(action, icon, label, badge) {
		var badgeHtml =
			badge && badge > 0
				? '<span class="ep-tile-badge' +
					(badge > 5 ? " ep-tile-badge--bad" : "") +
					'">' +
					esc(badge > 9 ? "9+" : badge) +
					"</span>"
				: "";
		return (
			'<button type="button" class="ep-tile" data-tile="' +
			esc(action) +
			'"><span class="ep-tile-icon"><i class="fa ' +
			esc(icon) +
			'"></i></span><span class="ep-tile-text">' +
			esc(label) +
			badgeHtml +
			"</span></button>"
		);
	}

	function bindTiles() {
		var host2 = host;
		Array.prototype.forEach.call(host2.querySelectorAll("[data-tile]"), function (el) {
			el.addEventListener("click", function () {
				show(el.dataset.tile);
			});
		});
		var offer = document.getElementById("bioEnable");
		if (offer) {
			offer.addEventListener("click", function () {
				runBiometric(offer);
			});
		}
		var later = document.getElementById("bioOfferNo");
		if (later) {
			later.addEventListener("click", function () {
				state.suggestBiometric = false;
				render();
			});
		}
		Array.prototype.forEach.call(host2.querySelectorAll(".ep-attn[data-screen]"), function (el) {
			el.addEventListener("click", function () {
				show(el.dataset.screen);
			});
		});
	}

	function renderPanel(action, key, draw, bind) {
		host.innerHTML =
			'<div class="ep-fade">' +
			screenBar("panelBack") +
			alertHtml() +
			loadingPanel() +
			"</div>";
		document.getElementById("panelBack").addEventListener("click", function () {
			show("dashboard");
		});
		call({ action: action, token: state.token })
			.then(function (data) {
				document.getElementById("panelBody").innerHTML = draw(data);
				// After the assignment, never before: `draw` only builds a string, and
				// the rows it renders do not exist in the document until this line has
				// run. Binding inside `draw` attaches every listener to nothing, and the
				// panel then looks finished and does nothing when tapped.
				(bind || bindPayslipRows)();
			})
			.catch(function (err) {
				if (expired(err && err.message)) return;
				document.getElementById("panelBody").innerHTML = emptyBlock(
					"fa-exclamation-triangle",
					"Something went wrong",
					(err && err.message) || ""
				);
			});
	}

	// ------------------------------------------------------------- attendance
	// Two calls rather than one, because the button's label and the list behind it
	// come from different questions: "what shift am I on today" is not "what days have
	// I worked". Filled together with Promise.all so the screen appears once, whole -
	// a tablet that showed the button before the list arrived would invite a tap the
	// employee had not yet seen the state of.
	function renderAttendance(note) {
		host.innerHTML =
			'<div class="ep-fade">' +
			screenBar("panelBack") +
			alertHtml() +
			(note ? infoAlert(note) : "") +
			loadingPanel() +
			"</div>";
		document.getElementById("panelBack").addEventListener("click", function () {
			show("dashboard");
		});
		Promise.all([
			call({ action: "my_shifts", token: state.token }),
			call({ action: "my_attendance", token: state.token }),
		])
			.then(function (both) {
				document.getElementById("panelBody").innerHTML = attendanceHtml(
					both[0],
					both[1]
				);
				// After the assignment, never before - see `renderPanel`.
				bindPunchButton();
			})
			.catch(function (err) {
				if (expired(err && err.message)) return;
				document.getElementById("panelBody").innerHTML = emptyBlock(
					"fa-exclamation-triangle",
					"Cannot read your attendance",
					(err && err.message) || ""
				);
			});
	}

	function attendanceHtml(shifts, att) {
		var today = att.today || { next: "in", punches: [], complete: false };
		var day = shifts.today_shift;

		var html = "";

		// No shift today is a refusal from the server, not a button to press. Offering
		// one anyway would be the dead click the brief rules out, so the reason is
		// printed instead.
		if (!day) {
			html +=
				'<div class="ep-alert ep-alert--bad"><i class="fa fa-exclamation-triangle"></i>' +
				"<span>You have no shift assigned today, so nothing can be recorded. Please contact HR.</span></div>";
		} else {
			html += '<div class="ep-card-title">Today</div>';
			html +=
				'<div class="ep-row"><div class="ep-row-main">' +
				'<div class="ep-row-title">' +
				esc(day.shift_type) +
				"</div>" +
				'<div class="ep-row-meta">' +
				esc(day.start_time) +
				" &ndash; " +
				esc(day.end_time) +
				'</div></div></div>';
			// Said before the tap, not after it. `punch` records an out-of-window tap
			// either way, but files it offshift where HR will see it as an exception, so
			// the employee is better off knowing that first.
			if (day.check_in_open === false) {
				html +=
					'<div class="ep-alert"><i class="fa fa-info-circle"></i><span>' +
					esc(t("Outside your punch window")) +
					" (" +
					esc(day.check_in_from) +
					" &ndash; " +
					esc(day.check_in_until) +
					"). " +
					esc(t("A tap now will be recorded for HR to review.")) +
					"</span></div>";
			}
			// Disabled once the day is complete, rather than left live: `punch` refuses a
			// third tap, so a pressable button there is a click guaranteed to fail.
			html +=
				'<button type="button" class="ep-btn" id="punchBtn"' +
				(today.next === "none" ? " disabled" : "") +
				"><i class=\"fa " +
				(today.next === "out" ? "fa-sign-out" : "fa-sign-in") +
				'"></i> ' +
				esc(punchLabel(today)) +
				"</button>";

			if ((today.punches || []).length) {
				html += '<div class="ep-mt-7">';
				today.punches.forEach(function (p) {
					html +=
						'<div class="ep-row-meta">' +
						esc(clockOnly(p.time)) +
						(p.offshift ? " &middot; outside your shift" : "") +
						"</div>";
				});
				html += "</div>";
			}
		}

		html += '<p class="ep-card-title ep-mt-1">Last ' +
			esc(att.from_date === att.to_date ? "day" : "31 days") +
			"</p>";
		var days = att.days || [];
		if (!days.length) {
			html += emptyBlock(
				"fa-calendar-o",
				"No days recorded yet",
				"Days appear here once you have tapped in and out."
			);
			return html;
		}
		days.forEach(function (d) {
			html +=
				'<div class="ep-row"><div class="ep-row-main">' +
				'<div class="ep-row-title">' +
				esc(d.date) +
				"</div>" +
				'<div class="ep-row-meta">' +
				esc(clockOnly(d.in_time)) +
				" &ndash; " +
				esc(clockOnly(d.out_time)) +
				(d.working_hours ? " &middot; " + esc(d.working_hours) + " h" : "") +
				"</div></div><span class=\"ep-badge " +
				tone(d.status) +
				'">' +
				esc(d.status) +
				"</span></div>";
		});
		return html;
	}

	function punchLabel(today) {
		if (today.next === "none") return "Today is recorded";
		if (today.next === "out") return "Tap to check out";
		return "Tap to check in";
	}

	function clockOnly(value) {
		// "2026-10-05 09:55:00.123456" -> "09:55". The seconds and the date are both
		// noise on a row that already sits under the day it belongs to.
		if (!value) return "--:--";
		var m = String(value).match(/(\d{2}:\d{2})/);
		return m ? m[1] : String(value);
	}

	function bindPunchButton() {
		var button = document.getElementById("punchBtn");
		if (!button) return;
		if (button.disabled) return;
		button.addEventListener("click", function () {
			doPunch(button);
		});
	}

	// Where the tablet thinks it is. The geofence is measured from the device, and a
	// tablet that will not or cannot answer still has to be able to record a day: this
	// resolves to `null` on denial, on timeout and on a browser with no location API at
	// all, and the server decides what a punch without coordinates is worth - that is
	// `geo_punch_enforce`, and it is HR's setting rather than a client's opinion.
	function punchPosition() {
		if (!navigator.geolocation) return Promise.resolve(null);
		return new Promise(function (resolve) {
			var settled = false;
			function done(value) {
				if (settled) return;
				settled = true;
				resolve(value);
			}
			navigator.geolocation.getCurrentPosition(
				function (position) {
					var coords = position && position.coords;
					if (!coords) return done(null);
					done({ latitude: coords.latitude, longitude: coords.longitude });
				},
				function () {
					done(null);
				},
				{ enableHighAccuracy: true, timeout: 8000, maximumAge: 60000 }
			);
			// A device that never answers at all - a tablet whose location service is off
			// rather than merely refused - can leave the callback unfired. Ten seconds on a
			// wall-mounted button is already long, and an employee standing there with a
			// spinner that will not end is the complaint that reaches HR.
			setTimeout(function () {
				done(null);
			}, 10000);
		});
	}

	function doPunch(button) {
		// Disabled for the round trip, not merely styled: the server collapses a
		// double-tap, but a button that stays live while the first request is in flight
		// invites the employee to wonder whether it worked.
		button.disabled = true;
		button.innerHTML = '<span class="ep-spin"></span> Finding you';
		punchPosition()
			.then(function (position) {
				button.innerHTML = '<span class="ep-spin"></span> Recording';
				var payload = { action: "punch", token: state.token };
				if (position) {
					payload.latitude = position.latitude;
					payload.longitude = position.longitude;
				}
				return call(payload);
			})
			.then(function (result) {
				// The screen is rebuilt from the server rather than patched from the
				// result: `punch` returns what it just did, but what the employee should
				// now see includes the day's hours, and only a re-read has those.
				state.error = result.offshift
					? "Recorded outside your shift. HR will review it."
					: "";
				renderAttendance(punchConfirmation(result));
			})
			.catch(function (err) {
				if (expired(err && err.message)) return;
				state.error = (err && err.message) || "Could not record that.";
				render();
			});
	}

	// What was just filed, in the employee's own terms, so the screen answers the question
	// the tap was asked with - "did my day start?" - rather than only redrawing the table.
	// The geofence verdict goes here too: being inside or outside the zone is a fact about
	// the punch the employee is entitled to see at the moment it is decided, and it is the
	// one thing a later payroll query cannot tell them they were not told.
	function punchConfirmation(result) {
		// The same two digits to the minute the rest of the screen shows a time with: a
		// confirmation that reads 09:55:00 next to a row that reads 09:55 looks like two
		// different clockings.
		var clock = result.time ? clockOnly(result.time) : "";
		// The clock goes in as an argument rather than being glued into the sentence.
		// "09:12" has no agreed place in a sentence in every language - in Arabic it
		// comes after the verb, and a glued phrase cannot say that.
		var out = result.kind === "OUT";
		var line = clock
			? t(out ? "Sign-out recorded at {0}" : "Sign-in recorded at {0}", [clock])
			: t(out ? "Sign-out recorded" : "Sign-in recorded");
		if (result.complete) line += " " + t("Your day is closed.");

		// `geo_status` is the code the server's fence returned, not a sentence: the words
		// belong here, where they can be said to the person holding the tablet, and the
		// codes are what `self_service.geo` names.
		if (result.geo_status === "unavailable") {
			line +=
				" " +
				t("No location from this tablet, so it was not checked against your site.");
		} else if (result.geo_status === "outside") {
			line += " " + t("This tablet was outside your site.");
		} else if (result.geo_status === "ok") {
			line += " " + t("Location verified.");
		}
		return line;
	}

	// ------------------------------------------------------------- performance
	// Draws only. Binding happens in `bindPerformance`, after the assignment - see
	// `renderPanel` for why that ordering is not optional.
	function renderPerformance(d) {
		var html = "";

		// Blockers come first and in full. An empty list with no explanation is
		// indistinguishable from a broken screen, and on this site it is the *expected*
		// state: every Performance table is empty until HR creates a cycle. Saying so
		// is the whole reason this panel is worth building before the data exists.
		(d.blockers || []).forEach(function (line) {
			html +=
				'<div class="ep-alert"><i class="fa fa-info-circle"></i><span>' +
				esc(line) +
				"</span></div>";
		});

		var appraisals = d.appraisals || [];
		html += '<p class="ep-card-title">My appraisals (' + appraisals.length + ")</p>";
		if (!appraisals.length) {
			html += emptyBlock(
				"fa-line-chart",
				"No appraisals yet",
				"Your HR team creates one for you when a cycle starts."
			);
		}
		appraisals.forEach(function (a) {
			html += appraisalHtml(a);
		});

		var goals = d.goals || [];
		html += '<p class="ep-card-title ep-mt-1">My goals (' + goals.length + ")</p>";
		if (!goals.length) {
			html += emptyBlock("fa-flag", "No goals set", "Goals come with your appraisal.");
		}
		goals.forEach(function (goal) {
			html +=
				'<div class="ep-row"><div class="ep-row-main">' +
				'<div class="ep-row-title">' +
				esc(goal.goal_name) +
				"</div>" +
				'<div class="ep-row-meta">' +
				esc(goal.status || "—") +
				" &middot; " +
				esc(goal.progress || 0) +
				"% complete" +
				(goal.kra ? " &middot; " + esc(goal.kra) : "") +
				'</div></div></div>';
		});
		return html;
	}

	function appraisalHtml(a) {
		var html =
			'<div class="ep-row"><div class="ep-row-main">' +
			'<div class="ep-row-title">' +
			esc(a.appraisal_cycle) +
			"</div>" +
			'<div class="ep-row-meta">' +
			esc(a.start_date || "") +
			(a.end_date ? " &ndash; " + esc(a.end_date) : "") +
			"</div>" +
			"</div><span class=\"ep-badge " +
			tone(a.status_label) +
			'">' +
			esc(a.status_label) +
			"</span></div>";

		if ((a.goals || []).length) {
			html += '<div style="margin:.4rem 0 .6rem">';
			a.goals.forEach(function (goal) {
				html +=
					'<div class="ep-row-meta">&bull; ' +
					esc(goal.kra) +
					" &middot; " +
					esc(goal.per_weightage) +
					"%</div>";
			});
			html += "</div>";
		}

		if (a.approval) {
			html +=
				'<div class="ep-row-meta">Awaiting approval: ' +
				esc(a.approval.status) +
				"</div>";
		}

		// Ratings are only editable on a draft, and the rows are the ones HRMS created -
		// the portal cannot add a criterion, so there is nothing to add here either.
		if ((a.self_ratings || []).length) {
			html +=
				'<div style="margin:.6rem 0"><p class="ep-card-title">Your ratings (' +
				esc(a.rated_count) +
				" of " +
				esc(a.rating_count) +
				")</p>";
			a.self_ratings.forEach(function (rating, index) {
				var saved = Math.round(Number(rating.rating) || 0);
				html +=
					'<div class="ep-row-meta" style="margin-bottom:.35rem">' +
					esc(rating.criteria) +
					" &middot; " +
					esc(rating.per_weightage) +
					"%</div><div class=\"ep-stars\" data-appraisal=\"" +
					esc(a.name) +
					"\" data-criterion=\"" +
					esc(rating.criteria) +
					'" data-index="' +
					index +
					'">';
				for (var star = 1; star <= 5; star++) {
					html +=
						'<button type="button" class="ep-star' +
						(star <= saved ? " ep-star--on" : "") +
						'" data-value="' +
						star +
						'"' +
						// A submitted appraisal cannot be saved from here, so its stars
						// are a display of the saved rating and not a control.
						(a.can_edit ? "" : " disabled") +
						'><i class="fa fa-star"></i></button>';
				}
				html +=
					'<span class="ep-star-value">' +
					esc(rating.rating || 0) +
					" / 5</span></div>";
			});
			html += "</div>";
		}

		if (a.can_edit) {
			html +=
				'<button type="button" class="ep-btn ep-btn-ghost" data-save-appraisal="' +
				esc(a.name) +
				'" style="margin:.4rem .4rem 0 0"><i class="fa fa-save"></i> Save ratings</button>';
			html +=
				'<button type="button" class="ep-btn" data-submit-appraisal="' +
				esc(a.name) +
				'" style="margin:.4rem 0 0"' +
				// Disabled until every criterion is rated. Offered-and-refused is the
				// dead click the brief rules out, so the button only lights up when it
				// will work.
				(a.can_submit ? "" : " disabled") +
				"><i class=\"fa fa-paper-plane\"></i> Submit self-assessment</button>";
		}
		return html;
	}

	function bindPerformance() {
		// Stars first, because clicking one changes whether Submit is available.
		Array.prototype.forEach.call(
			document.querySelectorAll(".ep-star"),
			function (button) {
				button.addEventListener("click", function () {
					pickStar(button);
				});
			}
		);
		Array.prototype.forEach.call(
			document.querySelectorAll("[data-save-appraisal]"),
			function (button) {
				button.addEventListener("click", function () {
					saveRatings(button);
				});
			}
		);
		Array.prototype.forEach.call(
			document.querySelectorAll("[data-submit-appraisal]"),
			function (button) {
				button.addEventListener("click", function () {
					submitAppraisal(button);
				});
			}
		);
	}

	// Ratings are held here rather than read back off the DOM at save time: the DOM is
	// a rendering of the server's list, and a save that posts whatever happens to be on
	// screen can post a rating the server never sent us.
	var starRatings = {};

	function pickStar(button) {
		var group = button.closest(".ep-stars");
		var appraisal = group.dataset.appraisal;
		var criterion = group.dataset.criterion;
		var value = parseInt(button.dataset.value, 10);
		starRatings[appraisal] = starRatings[appraisal] || {};
		starRatings[appraisal][criterion] = value;

		Array.prototype.forEach.call(group.querySelectorAll(".ep-star"), function (star) {
			var on = parseInt(star.dataset.value, 10) <= value;
			star.classList.toggle("ep-star--on", on);
		});
		var readout = group.querySelector(".ep-star-value");
		// Spoken here rather than left for the walker: the `5` in `4 / 5` is part of the
		// sentence, not a value, and a walker that turns every digit into a hole cannot
		// tell the two apart.
		if (readout) readout.textContent = t("{0} / 5", [value]);

		refreshSubmit(appraisal, group);
	}

	function refreshSubmit(appraisal, group) {
		var totals = document.querySelectorAll('.ep-stars[data-appraisal="' + appraisal + '"]');
		var button = document.querySelector('[data-submit-appraisal="' + appraisal + '"]');
		if (!button) return;
		var complete = true;
		Array.prototype.forEach.call(totals, function (each) {
			var picked = starRatings[appraisal] || {};
			// A criterion the employee has not touched this visit keeps whatever the
			// server already had, so the button is only disabled on criteria that are
			// rated zero *and* were never filled in before.
			if (!(each.dataset.criterion in picked)) {
				var shown = each.querySelector(".ep-star-value");
				if (shown && /^0 /.test(shown.textContent)) complete = false;
			}
		});
		button.disabled = !complete;
	}

	function saveRatings(button) {
		var name = button.dataset.saveAppraisal;
		button.disabled = true;
		call({
			action: "save_self_appraisal",
			token: state.token,
			name: name,
			ratings: Object.keys(starRatings[name] || {}).map(function (criterion) {
				return { criteria: criterion, rating: starRatings[name][criterion] };
			}),
		})
			.then(function (result) {
				state.error = "";
				delete starRatings[name];
				show("performance");
			})
			.catch(function (err) {
				if (expired(err && err.message)) return;
				fail(err);
			});
	}

	function submitAppraisal(button) {
		var name = button.dataset.submitAppraisal;
		button.disabled = true;
		button.innerHTML = '<span class="ep-spin"></span> Submitting';
		call({ action: "submit_appraisal", token: state.token, name: name })
			.then(function () {
				state.error = "";
				delete starRatings[name];
				show("performance");
			})
			.catch(function (err) {
				if (expired(err && err.message)) return;
				fail(err);
			});
	}

	/* Your own record.
	 *
	 * Two halves, because there are two different questions. "What does HR hold about
	 * me" is answered by the read-only block - company, department, manager, grade, all
	 * of it theirs to set. "Is the way you reach me still right" is answered by the form,
	 * and only the five fields in `DETAIL_FIELDS` on the server are in it: the portal
	 * writes an employee's contact details and nothing else, which is the same rule the
	 * server enforces by refusing every other name.
	 */
	var DETAIL_FIELDS = [
		["personal_email", "Personal email", "email"],
		["prefered_email", "Email payslips are sent to", "email"],
		["emergency_phone_number", "Emergency contact number", "tel"],
		["current_address", "Current address", "area"],
		["blood_group", "Blood group", "select"],
	];

	function renderDetails(note) {
		host.innerHTML =
			'<div class="ep-fade">' +
			screenBar("panelBack") +
			alertHtml() +
			(note ? infoAlert(note) : "") +
			loadingPanel() +
			"</div>";
		document.getElementById("panelBack").addEventListener("click", function () {
			show("dashboard");
		});
		call({ action: "my_details", token: state.token })
			.then(function (d) {
				document.getElementById("panelBody").innerHTML = detailsHtml(d);
				bindDetails(d);
			})
			.catch(function (err) {
				if (expired(err && err.message)) return;
				document.getElementById("panelBody").innerHTML = emptyBlock(
					"fa-exclamation-triangle",
					"Cannot read your details",
					(err && err.message) || ""
				);
			});
	}

	function detailsHtml(d) {
		var editable = d.editable || {};
		var read = [
			["Employee number", d.employee_number],
			["Designation", d.designation],
			["Department", d.department],
			["Company", d.company],
			["Joined", d.date_of_joining],
			["Gender", d.gender],
			["Marital status", d.marital_status],
		];

		var html =
			'<div class="ep-card ep-hero">' +
			'<div class="ep-hero-avatar">' +
			esc(initialsOf(d.employee_name)) +
			"</div>" +
			'<div class="ep-hero-text"><h1 class="ep-hero-name">' +
			esc(d.employee_name) +
			"</h1>" +
			'<p class="ep-hero-sub">' +
			esc([d.designation, d.department].filter(Boolean).join(" \u00b7 ")) +
			"</p></div></div>";

		html +=
			'<div class="ep-card"><p class="ep-card-title">As HR holds it</p><dl class="ep-kv">' +
			read
				.filter(function (row) {
					return row[1];
				})
				.map(function (row) {
					var value = /^\d{4}-\d{2}-\d{2}$/.test(String(row[1]))
						? dateOnly(row[1])
						: row[1];
					return (
						'<div class="ep-kv-row"><dt>' +
						esc(row[0]) +
						"</dt><dd>" +
						esc(value) +
						"</dd></div>"
					);
				})
				.join("") +
			"</dl>" +
			'<p class="ep-hint">Only your contact details below are yours to change here. Your number, department, grade and everything that decides your pay are set by HR.</p></div>';

		html += deviceCardHtml();

		html += '<div class="ep-card"><p class="ep-card-title">Your contact details</p><form id="detailsForm" novalidate>';
		DETAIL_FIELDS.forEach(function (field) {
			var name = field[0];
			var value = editable[name] || "";
			html +=
				'<div class="ep-field"><label class="ep-label" for="' + name + '">' +
				esc(field[1]) +
				"</label>";
			if (field[2] === "area") {
				html +=
					'<textarea class="ep-input" id="' + name + '" rows="3">' +
					esc(value) +
					"</textarea>";
			} else if (field[2] === "select") {
				html +=
					'<select class="ep-input" id="' + name + '"><option value="">' +
					"Not recorded</option>" +
					(d.blood_groups || [])
						// The doctype's options begin with an empty line, which would
						// otherwise be a second "Not recorded" in the list.
						.filter(Boolean)
						.map(function (option) {
							return (
								'<option value="' + esc(option) + '"' +
								(option === value ? " selected" : "") +
								">" +
								esc(option) +
								"</option>"
							);
						})
						.join("") +
					"</select>";
			} else {
				html +=
					'<input class="ep-input" id="' + name + '" type="' + field[2] +
					'" value="' + esc(value) + '" />';
			}
			html += "</div>";
		});
		html +=
			'<button type="submit" class="ep-btn" id="saveDetails">Save changes</button></form></div>';

		return html;
	}

	// Bound after the html is in the document, for the same reason every other screen
	// binds late: while the markup is being built, the form does not exist yet.
	function bindDetails(d) {
		var form = document.getElementById("detailsForm");
		if (form) {
			form.addEventListener("submit", function (e) {
				e.preventDefault();
				saveDetails(d);
			});
		}

		var enable = document.getElementById("bioEnable");
		if (enable) {
			enable.addEventListener("click", function () {
				runBiometric(enable);
			});
		}

		var disable = document.getElementById("bioDisable");
		if (disable) {
			disable.addEventListener("click", function () {
				// The key lives on the employee's record, so turning it off is a write to
				// the server: dropping the local id alone would leave the door standing and
				// tell the employee it had been shut.
				busy(disable, "Turning off");
				call({ action: "biometric_remove", token: state.token, credential_id: state.credentialId })
					.then(function () {
						dropCredential();
						renderDetails("Biometric sign-in is off. This tablet will ask for your numbers again.");
					})
					.catch(fail);
			});
		}

		var forget = document.getElementById("forgetLoginBtn");
		if (forget) {
			forget.addEventListener("click", function () {
				// Numbers alone, and no round trip: nothing about them was ever sent to the
				// server to be forgotten. The session is left where it is, because the person
				// using the tablet right now is not the reason the box was unticked.
				forgetIdentity();
				renderDetails("Done. This tablet will ask for your numbers again.");
			});
		}
	}

	// The whole point of remembering a session on a shared floor is that nobody else can
	// use it, and a ticked box alone does not achieve that. This card is where the choice
	// is offered, undone, and explained - rather than being decided silently at sign-in.
	function deviceCardHtml() {
		var html = '<div class="ep-card"><p class="ep-card-title">On this tablet</p>';

		if (state.credentialId) {
			return (
				html +
				'<p class="ep-quiet ep-device-on"><i class="fa fa-check-circle"></i> Signing in asks ' +
				"this tablet for your fingerprint or face, and the key it checks is recorded against " +
				"your employee - HR can see it there and turn it off.</p>" +
				'<p class="ep-quiet">No session is kept on the device, so closing the tab ends it: the ' +
				"way back in is the reader, every time. Your National ID and Employee Number still work " +
				"whenever the reader says no.</p>" +
				'<button type="button" class="ep-btn ep-btn-ghost ep-mt-9" id="bioDisable">' +
				'<i class="fa fa-times"></i> Stop using biometric sign-in</button>' +
				installHint() +
				"</div>"
			);
		}

		if (state.bioReady) {
			return (
				html +
				'<button type="button" class="ep-btn ep-btn-ghost ep-mt-9" id="bioEnable">' +
				'<i class="fa fa-fingerprint"></i> Unlock with fingerprint or face</button>' +
				'<p class="ep-hint">The key is stored on your employee record and checked by the ' +
				"server, so this tablet stops handing over your session to whoever picks it up. It " +
				"does not replace your National ID and Employee Number - those stay the way in " +
				"whenever the reader says no.</p>" +
				(sessionLine() + savedLoginHtml()) +
				installHint() +
				"</div>"
			);
		}

		return (
			html +
			'<p class="ep-hint">No fingerprint or face reader is reaching this browser. The ' +
			"reader is only available to a page served over https.</p>" +
			(sessionLine() + savedLoginHtml()) +
			installHint() +
			"</div>"
		);
	}

	// How this session is being held, and whether the tablet also holds the numbers: said
	// once rather than implied by a checkbox the employee ticked two screens ago.
	function sessionLine() {
		if (state.remembered) {
			return '<p class="ep-quiet">This session stays on this tablet until you sign out.</p>';
		}
		return (
			'<p class="ep-quiet">This session ends with the tab, which is the right setting for a ' +
			"tablet several people pick up.</p>"
		);
	}

	// The numbers outlive the session, which is the whole reason they are here: a ticked
	// sign-in expires once a day and the morning after it is the fourteen digits that have
	// to be found again. Say so, and give the way back out on the same screen, because
	// "someone else will use this tablet" is a decision taken after the box was ticked.
	// A tablet with a key in its reader never reaches this - enrolling took the numbers out
	// with the session, and `restoreIdentity` has nothing left to answer.
	function savedLoginHtml() {
		if (!restoreIdentity()) return "";
		return (
			'<p class="ep-quiet">Your National ID and Employee Number are kept on this tablet, so ' +
			"the sign-in box arrives filled in.</p>" +
			'<button type="button" class="ep-btn ep-btn-ghost ep-mt-9" id="forgetLoginBtn">' +
			'<i class="fa fa-user-times"></i> Forget these numbers</button>'
		);
	}

	function runBiometric(button) {
		busy(button, "Follow the prompt on screen");
		enrollBiometric()
			.then(function () {
				state.suggestBiometric = false;
				renderDetails("Ready. This tablet will ask for your fingerprint or face before it signs you in.");
			})
			.catch(function (err) {
				if (expired(err && err.message)) return;
				state.error = (err && err.message) || "The tablet would not agree.";
				render();
			});
	}

	// Offered on the screen a person is already looking at after ticking the box, rather
	// than as a dialog that interrupts the dashboard they came for.
	function biometricOfferHtml() {
		if (!state.suggestBiometric || !state.remembered) return "";
		if (state.credentialId || !state.bioReady) return "";
		return (
			'<div class="ep-alert ep-alert--info ep-device-offer">' +
			'<i class="fa fa-fingerprint"></i><span>Next time, unlock this tablet with your ' +
			"fingerprint instead of typing your numbers.</span>" +
			'<button type="button" class="ep-btn ep-btn-ghost" id="bioEnable">Turn on</button>' +
			'<button type="button" class="ep-btn ep-btn-ghost" id="bioOfferNo">Not now</button>' +
			"</div>"
		);
	}

	function saveDetails(d) {
		var before = d.editable || {};
		var fields = {};
		DETAIL_FIELDS.forEach(function (field) {
			var input = document.getElementById(field[0]);
			if (!input) return;
			var value = String(input.value || "").trim();
			// Only what actually moved is sent. An unchanged blank address is not a
			// request to write a blank over whatever HR holds.
			if (value !== String(before[field[0]] || "").trim()) {
				fields[field[0]] = value;
			}
		});

		if (!Object.keys(fields).length) {
			renderDetails("Nothing changed.");
			return;
		}

		var button = document.getElementById("saveDetails");
		button.disabled = true;
		button.innerHTML = '<span class="ep-spin"></span> Saving';

		var payload = { action: "update_my_details", token: state.token };
		Object.keys(fields).forEach(function (key) {
			payload[key] = fields[key];
		});

		call(payload)
			.then(function (result) {
				var saved = (result && result.updated) || Object.keys(fields);
				toast(t("Details saved"), "success");
				// The field names are the record's own and are not the portal's prose, so
				// only the lead-in is translated - and it is translated here rather than by
				// the walker, because a one-word row could never be told from the beginning
				// of the sentence it is standing in.
				renderDetails(t("Saved:") + " " + saved.join(", ") + ".");
			})
			.catch(function (err) {
				if (expired(err && err.message)) return;
				// The form is left as it was typed, so a correction refused for one field
				// does not also throw away the four that were right.
				button.disabled = false;
				button.innerHTML = "Save changes";
				state.error = (err && err.message) || "The details could not be saved.";
				render();
			});
	}

	function renderBalances() {
		host.innerHTML =
			'<div class="ep-fade">' +
			screenBar("panelBack") +
			alertHtml() +
			loadingPanel() +
			"</div>";
		document.getElementById("panelBack").addEventListener("click", function () {
			show("dashboard");
		});
		call({ action: "my_balances", token: state.token })
			.then(function (d) {
				document.getElementById("panelBody").innerHTML = balancesHtml(d);
			})
			.catch(function (err) {
				if (expired(err && err.message)) return;
				document.getElementById("panelBody").innerHTML = emptyBlock(
					"fa-exclamation-triangle",
					"Cannot read your balances",
					(err && err.message) || ""
				);
			});
	}





	/* The directory.
	 *
	 * One read fills the whole screen, and the toolbar narrows what that read already
	 * returned. Every field the search looks at - name, number, designation,
	 * department, mobile, email, manager - is in the rows that arrived the first time,
	 * so asking the server again per keystroke was a request per letter, on a device
	 * that shares one connection at a time and is often offline. */
	function renderColleagues() {
		host.innerHTML =
			'<div class="ep-fade">' +
			screenBar("panelBack") +
			alertHtml() +
			'<div id="panelBody">' + loadingBlock(3) + '</div>' +
			"</div>";
		document.getElementById("panelBack").addEventListener("click", function () {
			show("dashboard");
		});

		if (state.directory) {
			paintDirectory();
			return;
		}
		call({ action: "my_colleagues", token: state.token })
			.then(function (d) {
				state.directory = {
					rows: d.colleagues || [],
					departments: d.departments || [],
				};
				paintDirectory();
			})
			.catch(function (err) {
				if (expired(err && err.message)) return;
				document.getElementById("panelBody").innerHTML = emptyBlock(
					"fa-exclamation-triangle",
					"Cannot read colleagues",
					(err && err.message) || ""
				);
			});
	}

	function paintDirectory() {
		var body = document.getElementById("panelBody");
		var dir = state.directory;
		if (!body || !dir) return;

		var rows = dir.rows;
		if (!rows.length) {
			body.innerHTML = emptyBlock(
				"fa-users",
				"No colleagues yet",
				"No other active employees in your company."
			);
			return;
		}

		body.innerHTML =
			'<div class="ep-dir-toolbar">' +
			'<div class="ep-field"><label class="ep-label" for="dirSearch">Find someone</label>' +
			'<input class="ep-input" id="dirSearch" type="search" autocomplete="off" ' +
			'placeholder="Name, number, designation, email" /></div>' +
			'<div class="ep-field"><label class="ep-label" for="dirDept">Department</label>' +
			'<select class="ep-input" id="dirDept"><option value="">All departments</option>' +
			dir.departments
				.map(function (dept) {
					return '<option value="' + esc(dept) + '">' + esc(dept) + "</option>";
				})
				.join("") +
			"</select></div>" +
			'<p class="ep-dir-count" id="dirCount"></p>' +
			"</div>" +
			'<div id="dirList"></div>';

		var search = document.getElementById("dirSearch");
		var dept = document.getElementById("dirDept");
		var count = document.getElementById("dirCount");
		var list = document.getElementById("dirList");

		function refresh() {
			var found = filterDirectory(rows, search.value, dept.value);
			count.textContent =
				found.length === rows.length
					? rows.length + " colleagues, grouped by department"
					: found.length + " of " + rows.length + " colleagues";
			list.innerHTML = directoryGroupsHtml(found);
		}

		search.addEventListener("input", refresh);
		dept.addEventListener("change", refresh);
		refresh();
	}

	function filterDirectory(rows, text, dept) {
		var needle = String(text || "").trim().toLowerCase();
		return rows.filter(function (c) {
			if (dept && (c.department || "") !== dept) return false;
			if (!needle) return true;
			return [
				c.employee_name,
				c.employee_number,
				c.designation,
				c.department,
				c.company_email,
				c.cell_number,
				c.manager_name,
			]
				.filter(Boolean)
				.join(" ")
				.toLowerCase()
				.indexOf(needle) !== -1;
		});
	}

	// Grouped by department rather than one long alphabetical list: on a wall tablet
	// next to the workshop door, "who is in Facilities" is the question, and scrolling
	// past three hundred unrelated names to answer it is not a search.
	function directoryGroupsHtml(rows) {
		if (!rows.length) {
			return emptyBlock(
				"fa-search",
				"Nobody matches that",
				"Try part of a name, an employee number, or another department."
			);
		}
		var groups = {};
		var order = [];
		rows.forEach(function (c) {
			var key = c.department || "No department";
			if (!groups[key]) {
				groups[key] = [];
				order.push(key);
			}
			groups[key].push(c);
		});
		return order
			.map(function (key) {
				return (
					'<section class="ep-dir-group">' +
					'<h2 class="ep-dir-group-title"><i class="fa fa-building-o"></i> ' +
					esc(key) +
					"<span>" +
					groups[key].length +
					"</span></h2>" +
					'<div class="ep-colleagues-grid">' +
					groups[key].map(colleagueCardHtml).join("") +
					"</div></section>"
				);
			})
			.join("");
	}

	function colleagueCardHtml(c) {
		var meta = "";
		function line(icon, value) {
			if (!value) return;
			meta +=
				'<li><i class="fa ' +
				esc(icon) +
				'"></i><span>' +
				esc(value) +
				"</span></li>";
		}

		line("fa-id-badge", c.employee_number);
		line("fa-mobile", c.cell_number);
		line("fa-envelope-o", c.company_email);
		line("fa-share-alt", c.manager_name ? "Reports to " + c.manager_name : "");
		line("fa-calendar-o", c.date_of_joining ? "Joined " + dateOnly(c.date_of_joining) : "");

		var actions = "";
		if (c.cell_number) {
			actions +=
				'<a class="ep-dir-act" href="tel:' +
				esc(telHref(c.cell_number)) +
				'"><i class="fa fa-phone"></i> Call</a>';
		}
		if (c.company_email) {
			actions +=
				'<a class="ep-dir-act" href="mailto:' +
				esc(c.company_email) +
				'"><i class="fa fa-envelope-o"></i> Email</a>';
		}

		return (
			'<article class="ep-colleague-card' +
			(c.is_me ? " ep-colleague-card--me" : "") +
			'">' +
			'<div class="ep-colleague-avatar">' +
			(c.image ? '<img src="' + esc(c.image) + '" alt="" />' : esc(initialsOf(c.employee_name))) +
			"</div>" +
			'<div class="ep-colleague-body">' +
			'<div class="ep-colleague-name"><span>' +
			esc(c.employee_name) +
			"</span>" +
			(c.is_me ? '<span class="ep-you-badge">You</span>' : "") +
			"</div>" +
			'<div class="ep-colleague-desig">' +
			esc(c.designation || "") +
			"</div>" +
			(meta ? '<ul class="ep-meta">' + meta + "</ul>" : "") +
			(actions ? '<div class="ep-colleague-actions">' + actions + "</div>" : "") +
			"</div></article>"
		);
	}

	// `tel:` wants digits, and the numbers on these records are written the way people
	// dial them by hand - spaces, brackets, a leading 00.
	function telHref(value) {
		var digits = String(value).replace(/[^\d+]/g, "");
		return digits.replace(/^00/, "+");
	}


	function renderOrgChart() {
		host.innerHTML =
			'<div class="ep-fade">' +
			screenBar("panelBack") +
			'<div class="ep-org-toolbar">' +
			'<button type="button" class="ep-btn ep-btn-ghost" id="orgExpandAll"><i class="fa fa-expand"></i> Expand All</button>' +
			'<button type="button" class="ep-btn ep-btn-ghost" id="orgCollapseAll"><i class="fa fa-compress"></i> Collapse All</button>' +
			'<button type="button" class="ep-btn ep-btn-ghost" id="orgFindMe"><i class="fa fa-crosshairs"></i> Find Me</button>' +
			'<span class="ep-toolbar-spacer"></span>' +
			'<span id="orgStats" class="ep-toolbar-stat"></span>' +
			'</div>' +
			alertHtml() +
			'<div class="ep-card" id="panelBody" style="overflow:auto">' + loadingBlock(3) + '</div>' +
			"</div>";
		document.getElementById("panelBack").addEventListener("click", function () {
			show("dashboard");
		});
		call({ action: "my_org_chart", token: state.token })
			.then(function (d) {
				var body = document.getElementById("panelBody");
				body.innerHTML = orgChartHtml(d);
				bindOrgChart(d);
				updateOrgStats(d);
			})
			.catch(function (err) {
				if (expired(err && err.message)) return;
				document.getElementById("panelBody").innerHTML = emptyBlock(
					"fa-exclamation-triangle",
					"Cannot load org chart",
					(err && err.message) || ""
				);
			});
	}

	function orgChartHtml(d) {
		var tree = d.tree || [];
		if (!tree.length) {
			return emptyBlock("fa-sitemap", "No org data", "No active employees in your company.");
		}
		var html = '<div class="ep-org-chart">';
		tree.forEach(function (node) {
			html += renderOrgNode(node, 0, true);
		});
		html += "</div>";
		return html;
	}

	function renderOrgNode(node, depth, isRoot) {
		// `.ep-org-avatar` already carries the gradient and the initials type, so an
		// employee with no photo needs no markup of their own beside one with a photo.
		var avatar = node.image
			? '<img src="' + esc(node.image) + '" alt="" />'
			: esc(initialsOf(node.employee_name));
		var meBadge = node.is_me ? '<span class="ep-org-me-badge">You</span>' : '';
		var pathClass = node.in_my_path ? ' ep-org-in-path' : '';
		var hasChildren = node.children && node.children.length > 0;
		var toggleHtml = hasChildren
			? '<button type="button" class="ep-org-toggle" data-node="' + esc(node.name) + '" aria-expanded="true"><i class="fa fa-chevron-down"></i></button>'
			: '<span class="ep-org-toggle-placeholder"></span>';
		var connectorClass = depth > 0 ? ' ep-org-has-parent' : '';
		var html =
			'<div class="ep-org-node' + pathClass + connectorClass + '" data-name="' + esc(node.name) + '" style="--depth:' + depth + '">' +
			'<div class="ep-org-card' + (node.is_me ? ' ep-org-me' : '') + '">' +
			toggleHtml +
			'<div class="ep-org-avatar">' + avatar + '</div>' +
			'<div class="ep-org-info">' +
			'<div class="ep-org-name">' + esc(node.employee_name) + ' ' + meBadge + '</div>' +
			'<div class="ep-org-desig">' + esc(node.designation || "") + '</div>' +
			(node.department ? '<div class="ep-org-dept"><i class="fa fa-building-o"></i> ' + esc(node.department) + '</div>' : '') +
			(node.employee_number ? '<div class="ep-org-empnum"><i class="fa fa-id-badge"></i> ' + esc(node.employee_number) + '</div>' : '') +
			'</div>' +
			'</div>';
		if (hasChildren) {
			html += '<div class="ep-org-children">';
			node.children.forEach(function (child) {
				html += renderOrgNode(child, depth + 1, false);
			});
			html += '</div>';
		}
		html += '</div>';
		return html;
	}

	function bindOrgChart(d) {
		var body = document.getElementById("panelBody");
		if (!body) return;

		// Toggle handlers
		body.querySelectorAll(".ep-org-toggle").forEach(function (btn) {
			btn.addEventListener("click", function () {
				var nodeName = btn.dataset.node;
				var nodeEl = body.querySelector('.ep-org-node[data-name="' + nodeName + '"]');
				var childrenEl = nodeEl ? nodeEl.querySelector(".ep-org-children") : null;
				if (!childrenEl) return;
				var expanded = childrenEl.style.display !== "none";
				childrenEl.style.display = expanded ? "none" : "block";
				btn.setAttribute("aria-expanded", !expanded);
				btn.querySelector("i").className = expanded ? "fa fa-chevron-right" : "fa fa-chevron-down";
			});
		});

		// Toolbar buttons
		var expandAll = document.getElementById("orgExpandAll");
		var collapseAll = document.getElementById("orgCollapseAll");
		var findMe = document.getElementById("orgFindMe");

		if (expandAll) {
			expandAll.addEventListener("click", function () {
				body.querySelectorAll(".ep-org-children").forEach(function (el) { el.style.display = "block"; });
				body.querySelectorAll(".ep-org-toggle").forEach(function (btn) {
					btn.setAttribute("aria-expanded", "true");
					btn.querySelector("i").className = "fa fa-chevron-down";
				});
			});
		}
		if (collapseAll) {
			collapseAll.addEventListener("click", function () {
				body.querySelectorAll(".ep-org-children").forEach(function (el) { el.style.display = "none"; });
				body.querySelectorAll(".ep-org-toggle").forEach(function (btn) {
					btn.setAttribute("aria-expanded", "false");
					btn.querySelector("i").className = "fa fa-chevron-right";
				});
			});
		}
		if (findMe) {
			findMe.addEventListener("click", function () {
				var meEl = body.querySelector(".ep-org-me");
				if (meEl) {
					// Expand all ancestors
					var parent = meEl.closest(".ep-org-node");
					while (parent) {
						var children = parent.querySelector(".ep-org-children");
						if (children) children.style.display = "block";
						var toggle = parent.querySelector(".ep-org-toggle");
						if (toggle) {
							toggle.setAttribute("aria-expanded", "true");
							toggle.querySelector("i").className = "fa fa-chevron-down";
						}
						parent = parent.parentElement?.closest(".ep-org-node");
					}
					meEl.scrollIntoView({ behavior: "smooth", block: "center" });
					meEl.classList.add("ep-org-flash");
					setTimeout(function () { meEl.classList.remove("ep-org-flash"); }, 2000);
				}
			});
		}
	}

	function updateOrgStats(d) {
		var statsEl = document.getElementById("orgStats");
		if (!statsEl) return;
		var total = d.total || 0;
		var pathLen = (d.my_path || []).length;
		statsEl.textContent = total + " employees" + (pathLen ? " · " + pathLen + " levels to top" : "");
	}

	function renderTimesheets() {
		host.innerHTML =
			'<div class="ep-fade">' +
			screenBar("panelBack", actionButton("newTimesheet", "fa-plus", "Log Time")) +
			alertHtml() +
			loadingPanel() +
			"</div>";
		document.getElementById("panelBack").addEventListener("click", function () {
			show("dashboard");
		});
		document.getElementById("newTimesheet").addEventListener("click", function () {
			show("timesheet_form");
		});
		call({ action: "my_timesheets", token: state.token })
			.then(function (d) {
				document.getElementById("panelBody").innerHTML = timesheetsHtml(d);
			})
			.catch(function (err) {
				if (expired(err && err.message)) return;
				document.getElementById("panelBody").innerHTML = emptyBlock(
					"fa-exclamation-triangle",
					"Cannot read timesheets",
					(err && err.message) || ""
				);
			});
	}

	function timesheetsHtml(d) {
		var rows = d.timesheets || [];
		if (!rows.length) {
			return emptyBlock("fa-clock-o", "No timesheets yet", "Your logged time will appear here.");
		}
		var html = '<p class="ep-card-title">Timesheets</p>';
		rows.forEach(function (r) {
			var badgeClass = r.status === "Submitted" ? "ep-badge--info" : r.status === "Approved" ? "ep-badge--good" : r.status === "Rejected" ? "ep-badge--bad" : "";
			html +=
				'<div class="ep-row ep-row--head">' +
				'<div class="ep-row-main">' +
				'<div class="ep-row-title">' + esc(dateOnly(r.start_date)) + " &rarr; " + esc(dateOnly(r.end_date)) + '</div>' +
				'<div class="ep-row-meta">' + esc(r.status || "Draft") + (r.total_hours ? " &middot; " + esc(r.total_hours) + " hrs" : "") + '</div>' +
				'</div>' +
				'<span class="ep-badge ' + badgeClass + '">' + esc(r.total_hours || 0) + ' hrs</span>' +
				"</div>";
		});
		return html;
	}

	function renderTimesheetForm() {
		host.innerHTML =
			'<div class="ep-fade">' +
			screenBar("formBack") +
			loadingPanel() +
			"</div>";
		document.getElementById("formBack").addEventListener("click", function () {
			show("timesheets");
		});
		call({ action: "activity_types", token: state.token })
			.then(function (d) {
				var types = (d.activity_types || []).map(function (row) {
					return '<option value="' + esc(row.name) + '">' + esc(row.activity_type || row.name) + '</option>';
				}).join('');
				var today = new Date().toISOString().split("T")[0];
				document.getElementById("panelBody").innerHTML =
					'<p class="ep-card-title">Log Time</p>' +
					'<form id="timesheetForm" novalidate>' +
					'<div class="ep-field"><label class="ep-label" for="tsDate">Date</label>' +
					'<input class="ep-input" id="tsDate" type="date" value="' + today + '" required /></div>' +
					'<div class="ep-field"><label class="ep-label" for="tsActivity">Activity Type</label>' +
					'<select class="ep-input" id="tsActivity" required>' + types + '</select></div>' +
					'<div class="ep-field"><label class="ep-label" for="tsHours">Hours</label>' +
					'<input class="ep-input" id="tsHours" type="number" step="0.25" min="0.25" max="24" value="8" required /></div>' +
					'<div class="ep-field"><label class="ep-label" for="tsDesc">Description (optional)</label>' +
					'<textarea class="ep-input" id="tsDesc" rows="3" placeholder="What did you work on?"></textarea></div>' +
					'<button class="ep-btn" type="submit">Submit Timesheet</button></form>';
				document.getElementById("timesheetForm").addEventListener("submit", function (e) {
					e.preventDefault();
					submitTimesheetForm();
				});
			})
			.catch(function (err) {
				if (expired(err && err.message)) return;
				document.getElementById("panelBody").innerHTML = emptyBlock(
					"fa-exclamation-triangle",
					"Cannot load activity types",
					(err && err.message) || ""
				);
			});
	}

	function submitTimesheetForm() {
		call({
			action: "submit_timesheet",
			token: state.token,
			start_date: document.getElementById("tsDate").value,
			end_date: document.getElementById("tsDate").value,
			hours: parseFloat(document.getElementById("tsHours").value),
			activity_type: document.getElementById("tsActivity").value,
			description: document.getElementById("tsDesc").value,
		})
			.then(function () {
				toast(t("Timesheet submitted."), "success");
				show("timesheets");
			})
			.catch(function (err) {
				if (expired(err && err.message)) return;
				var body = document.getElementById("panelBody");
				if (body) {
					body.innerHTML =
						'<div class="ep-alert ep-alert--bad"><i class="fa fa-exclamation-triangle"></i><span>' +
						esc(err.message) +
						"</span></div>" + body.innerHTML;
				}
			});
	}

	function renderOvertime() {
		host.innerHTML =
			'<div class="ep-fade">' +
			screenBar("panelBack", actionButton("newOvertime", "fa-plus", "Request overtime")) +
			alertHtml() +
			loadingPanel() +
			"</div>";
		document.getElementById("panelBack").addEventListener("click", function () {
			show("dashboard");
		});
		document.getElementById("newOvertime").addEventListener("click", function () {
			show("overtime_form");
		});
		call({ action: "my_overtime", token: state.token })
			.then(function (d) {
				document.getElementById("panelBody").innerHTML = overtimeHtml(d);
			})
			.catch(function (err) {
				if (expired(err && err.message)) return;
				document.getElementById("panelBody").innerHTML = emptyBlock(
					"fa-exclamation-triangle",
					"Cannot read overtime",
					(err && err.message) || ""
				);
			});
	}

	function overtimeHtml(d) {
		var rows = d.overtime || [];
		if (!rows.length) {
			return emptyBlock("fa-bolt", "No overtime requests", "Your overtime requests will appear here.");
		}
		var html = '<p class="ep-card-title">Overtime Requests</p>';
		rows.forEach(function (r) {
			var badgeClass = r.status === "Approved" ? "ep-badge--good" : r.status === "Rejected" ? "ep-badge--bad" : "ep-badge--info";
			html +=
				'<div class="ep-row ep-row--head">' +
				'<div class="ep-row-main">' +
				'<div class="ep-row-title">' + esc(dateOnly(r.date)) + '</div>' +
				'<div class="ep-row-meta">' + esc(r.status || "Draft") + (r.description ? " &middot; " + esc(r.description) : "") + '</div>' +
				'</div>' +
				'<span class="ep-badge ' + badgeClass + '">' + esc(r.hours || 0) + ' hrs</span>' +
				"</div>";
		});
		return html;
	}

	function renderOvertimeForm() {
		host.innerHTML =
			'<div class="ep-fade">' +
			screenBar("formBack") +
			loadingPanel() +
			"</div>";
		document.getElementById("formBack").addEventListener("click", function () {
			show("overtime");
		});
		var today = new Date().toISOString().split("T")[0];
		document.getElementById("panelBody").innerHTML =
			'<p class="ep-card-title">Request Overtime</p>' +
			'<form id="overtimeForm" novalidate>' +
			'<div class="ep-field"><label class="ep-label" for="otDate">Date</label>' +
			'<input class="ep-input" id="otDate" type="date" value="' + today + '" required /></div>' +
			'<div class="ep-field"><label class="ep-label" for="otHours">Hours</label>' +
			'<input class="ep-input" id="otHours" type="number" step="0.25" min="0.25" max="12" value="2" required /></div>' +
			'<div class="ep-field"><label class="ep-label" for="otReason">Reason</label>' +
			'<textarea class="ep-input" id="otReason" rows="3" placeholder="Why is overtime needed?" required></textarea></div>' +
			'<button class="ep-btn" type="submit">Submit Request</button></form>';
		document.getElementById("overtimeForm").addEventListener("submit", function (e) {
			e.preventDefault();
			submitOvertimeForm();
		});
	}

	function submitOvertimeForm() {
		call({
			action: "submit_overtime",
			token: state.token,
			date: document.getElementById("otDate").value,
			hours: parseFloat(document.getElementById("otHours").value),
			reason: document.getElementById("otReason").value,
		})
			.then(function () {
				toast(t("Overtime request submitted."), "success");
				show("overtime");
			})
			.catch(function (err) {
				if (expired(err && err.message)) return;
				var body = document.getElementById("panelBody");
				if (body) {
					body.innerHTML =
						'<div class="ep-alert ep-alert--bad"><i class="fa fa-exclamation-triangle"></i><span>' +
						esc(err.message) +
						"</span></div>" + body.innerHTML;
				}
			});
	}

	function renderLeaveCalendar() {
		host.innerHTML =
			'<div class="ep-fade">' +
			screenBar("panelBack") +
			'<div class="ep-cal-toolbar">' +
			'<label class="ep-label ep-cal-label" for="calMonth"><i class="fa fa-calendar"></i> Month</label>' +
			'<select class="ep-input ep-cal-select" id="calMonth"></select>' +
			'<label class="ep-label ep-cal-label" for="calYear"><i class="fa fa-calendar-o"></i> Year</label>' +
			'<select class="ep-input ep-cal-select" id="calYear"></select>' +
			'<span class="ep-toolbar-spacer"></span>' +
			'<span id="calStats" class="ep-toolbar-stat"></span>' +
			'</div>' +
			alertHtml() +
			loadingPanel("ep-card ep-card-scroll") +
			"</div>";
		document.getElementById("panelBack").addEventListener("click", function () {
			show("dashboard");
		});
		initCalendarSelectors();
		loadLeaveCalendar();
	}

	function initCalendarSelectors() {
		var monthSel = document.getElementById("calMonth");
		var yearSel = document.getElementById("calYear");
		var now = new Date();
		for (var m = 0; m < 12; m++) {
			var d = new Date(now.getFullYear(), m, 1);
			var opt = document.createElement("option");
			opt.value = m + 1;
			opt.textContent = d.toLocaleString("default", { month: "long" });
			if (m === now.getMonth()) opt.selected = true;
			monthSel.appendChild(opt);
		}
		for (var y = now.getFullYear() - 1; y <= now.getFullYear() + 1; y++) {
			var opt2 = document.createElement("option");
			opt2.value = y;
			opt2.textContent = y;
			if (y === now.getFullYear()) opt2.selected = true;
			yearSel.appendChild(opt2);
		}
		monthSel.addEventListener("change", loadLeaveCalendar);
		yearSel.addEventListener("change", loadLeaveCalendar);
	}

	function loadLeaveCalendar() {
		var month = parseInt(document.getElementById("calMonth").value, 10);
		var year = parseInt(document.getElementById("calYear").value, 10);
		call({ action: "my_leave_calendar", token: state.token, month: month, year: year })
			.then(function (d) {
				document.getElementById("panelBody").innerHTML = leaveCalendarHtml(d);
				updateCalStats(d);
			})
			.catch(function (err) {
				if (expired(err && err.message)) return;
				document.getElementById("panelBody").innerHTML = emptyBlock(
					"fa-exclamation-triangle",
					"Cannot load calendar",
					(err && err.message) || ""
				);
			});
	}

	function leaveCalendarHtml(d) {
		var rows = d.leaves || [];
		var byEmp = d.by_employee || {};
		if (!rows.length) {
			return emptyBlock("fa-calendar", "No approved leave this month", "Team leave will appear here when approved.");
		}
		// Group by date
		var byDate = {};
		rows.forEach(function (r) {
			var from = getdate(r.from_date);
			var to = getdate(r.to_date);
			var cur = new Date(from);
			while (cur <= to) {
				var key = cur.toISOString().split("T")[0];
				byDate[key] = byDate[key] || [];
				byDate[key].push(r);
				cur = add_days(cur, 1);
			}
		});
		var monthStart = getdate(d.month_start);
		var monthEnd = getdate(d.month_end);
		var firstDow = monthStart.getDay();
		var html = '<div class="ep-cal-grid">';
		DOW_NAMES.forEach(function (name) {
			html += '<div class="ep-cal-dow">' + name + "</div>";
		});
		// The blanks before the first of the month, so the 1st sits under its own
		// weekday rather than under whichever column the month happened to start in.
		for (var b = 0; b < firstDow; b++) {
			html += '<div class="ep-cal-day ep-cal-blank" aria-hidden="true"></div>';
		}
		var cur = new Date(monthStart);
		while (cur <= monthEnd) {
			var key = cur.toISOString().split("T")[0];
			var dayLeaves = byDate[key] || [];
			var isWeekend = cur.getDay() === 5 || cur.getDay() === 6; // Fri/Sat
			var isToday = key === dateOnly(new Date().toISOString());
			html +=
				'<div class="ep-cal-day' +
				(isWeekend ? " ep-cal-weekend" : "") +
				(isToday ? " ep-cal-today" : "") +
				(dayLeaves.length ? " ep-cal-has-leave" : "") +
				'">' +
				'<div class="ep-cal-date">' +
				cur.getDate() +
				"</div>";
			dayLeaves.forEach(function (lv) {
				html +=
					'<div class="ep-cal-leave">' +
					esc(lv.employee_name) +
					": " +
					esc(lv.leave_type) +
					"</div>";
			});
			html += "</div>";
			cur = add_days(cur, 1);
		}
		html += "</div>";
		return html;
	}

	var DOW_NAMES = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];

	function updateCalStats(d) {
		var statsEl = document.getElementById("calStats");
		if (!statsEl) return;
		var totalDays = Object.keys(d.by_employee || {}).length;
		var totalLeaves = (d.leaves || []).length;
		statsEl.textContent = totalLeaves + " leave entries across " + totalDays + " colleagues";
	}

	function getdate(dateStr) {
		if (!dateStr) return new Date();
		var parts = String(dateStr).split("-");
		return new Date(parseInt(parts[0]), parseInt(parts[1]) - 1, parseInt(parts[2]));
	}

	function add_days(date, days) {
		var result = new Date(date);
		result.setDate(result.getDate() + days);
		return result;
	}












	function renderAssets() {
		host.innerHTML =
			'<div class="ep-fade">' +
			screenBar("panelBack") +
			alertHtml() +
			loadingPanel() +
			"</div>";
		document.getElementById("panelBack").addEventListener("click", function () {
			show("dashboard");
		});
		call({ action: "my_assets", token: state.token })
			.then(function (d) {
				document.getElementById("panelBody").innerHTML = assetsHtml(d);
			})
			.catch(function (err) {
				if (expired(err && err.message)) return;
				document.getElementById("panelBody").innerHTML = emptyBlock(
					"fa-exclamation-triangle",
					"Cannot load assets",
					(err && err.message) || ""
				);
			});
	}

	function assetsHtml(d) {
		var assets = d.assets || [];
		var soon = new Date(Date.now() + 30 * 24 * 60 * 60 * 1000).toISOString().split("T")[0];

		var html =
			'<div class="ep-stats">' +
			stat(d.total_assets, "Assets") +
			stat(d.active_assets, "In service") +
			stat(d.insurance_due, "Insurance due") +
			"</div>";
		if (!assets.length) {
			return html + emptyBlock("fa-laptop", "No equipment assigned", "What HR has handed you will appear here.");
		}
		html += '<p class="ep-card-title">Assigned equipment</p>';
		assets.forEach(function (a) {
			var expiring = a.insurance_end_date && a.insurance_end_date < soon;
			var meta = "";
			function line(icon, value, tone) {
				if (!value) return;
				meta +=
					"<li" +
					(tone ? ' class="' + tone + '"' : "") +
					"><i class=\"fa " +
					esc(icon) +
					'"></i><span>' +
					esc(value) +
					"</span></li>";
			}
			line("fa-tag", a.asset_category);
			line("fa-building-o", a.department);
			line("fa-calendar", "Acquired " + dateOnly(a.purchase_date));
			line("fa-money", a.purchase_amount ? "Value " + a.purchase_amount : "");
			line(
				"fa-shield",
				a.insurance_end_date
					? "Insurance " +
						dateOnly(a.insurance_end_date) +
						(expiring ? " \u00b7 due soon" : "") +
						(a.insurer ? " \u00b7 " + a.insurer : "")
					: "",
				expiring ? "ep-meta--warn" : ""
			);
			line("fa-map-marker", a.location);

			html +=
				'<article class="ep-asset-card">' +
				'<div class="ep-asset-head"><span class="ep-asset-name">' +
				esc(a.asset_name || a.item_code) +
				'</span><span class="ep-badge ' +
				(a.status === "Submitted" ? "ep-badge--good" : "ep-badge--info") +
				'">' +
				esc(a.status) +
				"</span></div>" +
				'<ul class="ep-meta">' +
				meta +
				"</ul></article>";
		});
		return html;
	}



















	function balancesHtml(d) {
		var rows = d.balances || [];
		if (!rows.length) {
			return emptyBlock("fa-balance-scale", "No allocations yet", "Your leave balance will appear here once HR allocates it.");
		}
		var html =
			'<p class="ep-card-title">Leave balance &middot; ' + esc(d.year) + "</p>" +
			'<p class="ep-card-sub">' +
			esc(d.total_remaining) +
			" days remaining in total</p>";
		rows.forEach(function (r) {
			var pct = r.allocated > 0 ? Math.round((r.used / r.allocated) * 100) : 0;
			var tone = r.remaining <= 0 ? "ep-badge--bad" : r.remaining <= 5 ? "ep-badge--warn" : "ep-badge--good";
			// Only said when the type actually carries forward. Telling somebody
			// "you may carry 0 days over" about a type that does not carry forward at
			// all would be a second, wrong claim.
			var carry =
				r.carry_allowed && r.carry_cap > 0
					? esc(r.carry_cap) +
						" days may carry over" +
					  (r.carry_expires ? " until " + esc(dateOnly(r.carry_expires)) : "")
					: "";
			html +=
				'<div class="ep-balance">' +
				'<div class="ep-row ep-row--plain">' +
				'<div class="ep-row-main"><div class="ep-row-title">' +
				esc(r.label || r.leave_type) +
				"</div>" +
				'<div class="ep-row-meta">' +
				esc(r.allocated) +
				" allocated &middot; " +
				esc(r.used) +
				" taken" +
				(carry ? " &middot; " + carry : "") +
				"</div></div>" +
				'<div class="ep-row-value"><span class="ep-badge ' +
				tone +
				'">' +
				esc(r.remaining) +
				" left</span></div></div>" +
				'<div class="ep-bar"><div class="ep-bar-fill" style="width:' +
				Math.min(pct, 100) +
				'%;background:var(--ep-' +
				(r.remaining <= 0 ? "bad" : r.remaining <= 5 ? "warn" : "accent") +
				')"></div></div></div>';
		});
		return html;
	}

	function renderTimeOff() {
		host.innerHTML =
			'<div class="ep-fade">' +
			screenBar("panelBack", actionButton("newRequest", "fa-plus", "Request time off")) +
			alertHtml() +
			loadingPanel() +
			"</div>";
		document.getElementById("panelBack").addEventListener("click", function () {
			show("dashboard");
		});
		document.getElementById("newRequest").addEventListener("click", function () {
			show("time_off_form");
		});
		call({ action: "my_leaves", token: state.token })
			.then(function (d) {
				document.getElementById("panelBody").innerHTML = leavesHtml(d);
				bindLeaves();
			})
			.catch(function (err) {
				if (expired(err && err.message)) return;
				document.getElementById("panelBody").innerHTML = emptyBlock(
					"fa-exclamation-triangle",
					"Cannot read your requests",
					(err && err.message) || ""
				);
			});
	}

	// A manager's own queue. Read-only on purpose, and said so on the screen rather than
	// left to be discovered by a missing button: approving is a decision about somebody
	// else's paid time, made in the desk where HRMS applies its permissions and keeps its
	// history - not on a wall tablet whoever is standing in front of next. What this
	// screen is for is knowing that something is waiting.
	function approvalsHtml(d) {
		var rows = d.approvals || [];
		if (!rows.length) {
			return emptyBlock(
				"fa-inbox",
				"Nothing waiting",
				"Leave requests you are named on will appear here."
			);
		}
		var html =
			'<p class="ep-card-title">Waiting on you (' + rows.length + ")</p>" +
			'<p class="ep-hint">Say yes or no from the HR desk - this list is what is outstanding, ' +
			"not a button to press.</p>";
		rows.forEach(function (r) {
			html +=
				'<div class="ep-row"><div class="ep-row-main">' +
				'<div class="ep-row-title">' +
				esc(r.employee_name || r.employee) +
				" &middot; " +
				esc(r.leave_type) +
				"</div>" +
				'<div class="ep-row-meta">' +
				esc(dateOnly(r.from_date)) +
				" &rarr; " +
				esc(dateOnly(r.to_date)) +
				(r.total_leave_days ? " &middot; " + esc(r.total_leave_days) + " days" : "") +
				(r.posting_date
					? " &middot; " + esc(t("asked {0}", [dateOnly(r.posting_date)]))
					: "") +
				"</div>" +
				(r.description
					? '<div class="ep-row-meta">' + esc(r.description) + "</div>"
					: "") +
				'</div><span class="ep-badge ep-badge--warn">Open</span></div>';
		});
		return html;
	}

	function leavesHtml(d) {
		var rows = d.leaves || [];
		if (!rows.length) {
			return emptyBlock("fa-calendar", "No requests yet", "Anything you request will be listed here.");
		}
		var html = '<p class="ep-card-title">Your requests</p>';
		rows.forEach(function (r) {
			html +=
				'<div class="ep-row"><div class="ep-row-main">' +
				'<div class="ep-row-title">' +
				esc(r.leave_type) +
				"</div>" +
				'<div class="ep-row-meta">' +
				esc(dateOnly(r.from_date)) +
				" &rarr; " +
				esc(dateOnly(r.to_date)) +
				(r.total_leave_days ? " &middot; " + esc(r.total_leave_days) + " days" : "") +
				'</div></div><span class="ep-badge ' +
				tone(r.status) +
				'">' +
				esc(r.status) +
				"</span>" +
				(r.status === "Open"
					? '<button type="button" class="ep-row-withdraw" data-name="' +
					  esc(r.name) +
					  '">Withdraw</button>'
					: "") +
				"</div>";
		});
		return html;
	}

	// A request that is still with the approver is the employee's to take back. Bound
	// after the rows exist, like every other listener here.
	function bindLeaves() {
		Array.prototype.forEach.call(
			host.querySelectorAll(".ep-row-withdraw"),
			function (el) {
				el.addEventListener("click", function () {
					el.disabled = true;
					call({
						action: "cancel_leave",
						token: state.token,
						name: el.dataset.name,
						reason: t("Withdrawn by the employee from the portal."),
					})
						.then(function () {
							toast(t("Request withdrawn."), "success");
							renderTimeOff();
						})
						.catch(function (err) {
							el.disabled = false;
							if (expired(err && err.message)) return;
							state.error = (err && err.message) || "Could not withdraw it.";
							renderTimeOff();
						});
				});
			}
		);
	}

	function tone(status) {
		var s = String(status || "").toLowerCase();
		if (s === "approved") return "ep-badge--good";
		if (s === "rejected" || s === "cancelled") return "ep-badge--bad";
		if (s === "open" || s === "pending") return "ep-badge--info";
		return "";
	}

	function renderTimeOffForm() {
		host.innerHTML =
			'<div class="ep-fade">' +
			screenBar("formBack") +
			loadingPanel() +
			"</div>";
		document.getElementById("formBack").addEventListener("click", function () {
			show("time_off");
		});
		call({ action: "leave_types", token: state.token })
			.then(function (d) {
				var offered = (d.leave_types || []).filter(function (row) {
					return row.has_allocation;
				});
				if (!offered.length) {
					document.getElementById("panelBody").innerHTML = emptyBlock(
						"fa-calendar-times-o",
						"No allocation covers today",
						"Please contact HR to have leave allocated."
					);
					return;
				}
				var options = offered
					.map(function (row) {
						return '<option value="' + esc(row.name) + '">' + esc(row.label || row.name) + "</option>";
					})
					.join("");
				document.getElementById("panelBody").innerHTML =
					'<p class="ep-card-title">Request time off</p>' +
					'<form id="leaveForm" novalidate>' +
					'<div class="ep-field"><label class="ep-label" for="lt">Leave type</label>' +
					'<select class="ep-input" id="lt" required>' +
					options +
					"</select></div>" +
					'<div class="ep-field"><label class="ep-label" for="fd">From</label>' +
					'<input class="ep-input" id="fd" type="date" required /></div>' +
					'<div class="ep-field"><label class="ep-label" for="td">To</label>' +
					'<input class="ep-input" id="td" type="date" required /></div>' +
					'<div class="ep-field"><label class="ep-label" for="rs">Reason (optional)</label>' +
					'<input class="ep-input" id="rs" /></div>' +
					'<button class="ep-btn" type="submit">Submit request</button></form>';
				document.getElementById("leaveForm").addEventListener("submit", function (e) {
					e.preventDefault();
					submitLeave();
				});
			})
			.catch(function (err) {
				if (expired(err && err.message)) return;
				document.getElementById("panelBody").innerHTML = emptyBlock(
					"fa-exclamation-triangle",
					"Cannot load leave types",
					(err && err.message) || ""
				);
			});
	}

	function submitLeave() {
		call({
			action: "request_leave",
			token: state.token,
			leave_type: document.getElementById("lt").value,
			from_date: document.getElementById("fd").value,
			to_date: document.getElementById("td").value,
			reason: document.getElementById("rs").value,
		})
			.then(function () {
				toast(t("Your request has been submitted."), "success");
				show("time_off");
			})
			.catch(function (err) {
				if (expired(err && err.message)) return;
				var host3 = document.getElementById("panelBody");
				if (!host3) return;
				// The alert goes into `panelBody` itself. It *is* the `.ep-card` on this
				// screen, so looking for a `.ep-card` inside it finds nothing and the
				// refusal is dropped without a word - which is exactly what a refused
				// submission must never do.
				host3.innerHTML =
					'<div class="ep-alert ep-alert--bad"><i class="fa fa-exclamation-triangle"></i><span>' +
					esc(err.message) +
					"</span></div>" + host3.innerHTML;
			});
	}

	// ----------------------------------------------------------------- expenses

	// The statuses an employee sees, mapped to a tone. `approval_status` is the one
	// that matters for an expense, because the claim's own `status` stays Draft right
	// through approval - so keying on it would show every claim in progress as though
	// nothing had happened to it.
	function claimTone(claim) {
		var approval = String(claim.approval_status || "").toLowerCase();
		if (approval === "approved") return "ep-badge--good";
		if (approval === "rejected") return "ep-badge--bad";
		if (approval === "cancelled") return "ep-badge--bad";
		if (approval === "draft") return "ep-badge--info";
		return "";
	}

	function claimLabel(claim) {
		var approval = String(claim.approval_status || "");
		if (approval === "Approved") return "Approved";
		if (approval === "Rejected") return "Rejected";
		if (approval === "Cancelled") return "Cancelled";
		if (claim.docstatus === 1) return claim.status || "Submitted";
		return "Waiting for approval";
	}

	function renderClaimList(d) {
		var rows = d.claims || [];
		// The button is built first and unconditionally. Returning the empty state
		// without it left an employee who has never claimed anything with no way to
		// start - the tile worked, the screen loaded, and the one thing to press was
		// missing.
		var html =
			'<p class="ep-card-title">My expense claims</p>' +
			'<button type="button" class="ep-btn ep-btn-ghost ep-mb-9" id="newClaim">' +
			'<i class="fa fa-plus"></i> Claim an expense</button>';
		if (!rows.length) {
			return (
				html +
				'<div class="ep-empty"><div class="ep-empty-icon"><i class="fa fa-money"></i></div>' +
				'<div class="ep-empty-title">Nothing claimed yet</div>' +
				'<div class="ep-quiet">Anything you claim will be listed here.</div></div>'
			);
		}
		rows.forEach(function (r) {
			// The amount shown is the sanctioned one once there is one, because that is
			// what will actually be paid. Showing the claimed figure on an approved claim
			// would overstate it wherever HR sanctioned less.
			var amount = r.total_sanctioned_amount || r.total_claimed_amount;
			html +=
				'<div class="ep-row ep-row--link" data-claim="' +
				esc(r.name) +
				'">' +
				'<div class="ep-row-main"><div class="ep-row-title">' +
				esc(dateOnly(r.posting_date)) +
				'</div><div class="ep-row-meta">' +
				esc(claimLabel(r)) +
				'</div></div><div class="ep-row-value">' +
				money(amount, r.currency) +
				' <i class="fa fa-chevron-right ep-row-go"></i></div></div>';
		});
		return html;
	}

	// Bound by `renderPanel` after the html is in the document, for the same reason as
	// `bindPayslipRows`: the rows do not exist while the html is being built.
	function bindClaimRows() {
		var body = document.getElementById("panelBody");
		if (!body) return;
		var newButton = document.getElementById("newClaim");
		if (newButton) {
			newButton.addEventListener("click", function () {
				show("expense_form");
			});
		}
		Array.prototype.forEach.call(body.querySelectorAll("[data-claim]"), function (el) {
			el.addEventListener("click", function () {
				openClaim(el.dataset.claim);
			});
		});
	}

	function openClaim(name) {
		var body = document.getElementById("panelBody");
		call({ action: "my_expense_claim", token: state.token, name: name })
			.then(function (d) {
				body.innerHTML = renderClaimDetail(d);
				var withdraw = document.getElementById("withdrawClaim");
				if (withdraw) {
					withdraw.addEventListener("click", function () {
						withdrawClaim(name, withdraw);
					});
				}
			})
			.catch(function (err) {
				if (expired(err && err.message)) return;
				body.innerHTML = emptyBlock(
					"fa-exclamation-triangle",
					"Cannot open this claim",
					(err && err.message) || ""
				);
			});
	}

	function renderClaimDetail(d) {
		var rows = d.items || [];
		var html =
			'<p class="ep-card-title">Expense claim</p>' +
			'<div class="ep-quiet ep-quiet--block">' +
			esc(d.name) +
			" &middot; " +
			esc(dateOnly(d.posting_date)) +
			'</div><span class="ep-badge ' +
			claimTone(d) +
			'">' +
			esc(claimLabel(d)) +
			"</span>";

		html +=
			'<p class="ep-card-title ep-mt-9"><i class="fa fa-list"></i> Lines</p>';
		if (!rows.length) {
			html +=
				'<div class="ep-quiet">No lines recorded.</div>';
		}
		rows.forEach(function (r) {
			// Both figures once they differ. A sanctioned line that came back lower than
			// claimed is the single thing an employee most needs to see on this screen,
			// and it is invisible if only the sanctioned amount is shown.
			var differs =
				Number(r.sanctioned_amount || 0) > 0 &&
				Number(r.sanctioned_amount) !== Number(r.amount);
			html +=
				'<div class="ep-row ep-row--tight">' +
				'<div class="ep-row-main"><div class="ep-row-title">' +
				esc(r.expense_type) +
				"</div>" +
				'<div class="ep-row-meta">' +
				esc(dateOnly(r.expense_date)) +
				(r.description ? " &middot; " + esc(r.description) : "") +
				(differs
					? " &middot; " + esc(t("claimed {0}", [money(r.amount, d.currency)]))
					: "") +
				"</div></div>" +
				'<div class="ep-row-value">' +
				money(differs ? r.sanctioned_amount : r.amount, d.currency) +
				"</div></div>";
		});

		if (d.receipts && d.receipts.length) {
			html +=
				'<p class="ep-card-title ep-mt-9"><i class="fa fa-paperclip"></i> Receipts</p>';
			d.receipts.forEach(function (r) {
				html +=
					'<div class="ep-row ep-row--tight"><div class="ep-row-main">' +
					esc(r.file_name) +
					"</div></div>";
			});
		}

		html +=
			'<div class="ep-row ep-row--total">' +
			'<div class="ep-row-main"><strong>Claimed</strong></div>' +
			'<div class="ep-row-value">' +
			money(d.total_claimed_amount, d.currency) +
			"</div></div>";
		// Shown only once there is a sanctioned amount to compare against. A claim
		// still awaiting approval has `total_sanctioned_amount` of 0, and printing
		// "Approved amount 0.00" beside a claim for 275.50 reads as though HR had
		// approved nothing at all rather than not yet decided.
		if (
			Number(d.total_sanctioned_amount) > 0 &&
			Number(d.total_sanctioned_amount) !== Number(d.total_claimed_amount)
		) {
			html +=
				'<div class="ep-row ep-row--tight">' +
				'<div class="ep-row-main">Approved amount</div>' +
				'<div class="ep-row-value">' +
				money(d.total_sanctioned_amount, d.currency) +
				"</div></div>";
		}

		// Only offered when the server says it may be done. The button's presence is the
		// claim about state; the server refuses regardless, because a client-side gate is
		// a suggestion and this decides whose money is involved.
		if (d.withdrawable) {
			// The claim is still with HR, so the button says so: an employee who
			// withdraws one has cancelled a request somebody is reading, and it is
			// better they know that than be surprised the claim disappears.
			var pending = String(d.approval_status || "") === "Draft" && d.docstatus === 0;
			html +=
				'<button type="button" class="ep-btn ep-btn-ghost ep-mt-1" id="withdrawClaim">' +
				'<i class="fa fa-' +
				(pending ? "times" : "trash-o") +
				'"></i> ' +
				(pending ? "Cancel this request" : "Withdraw this claim") +
				"</button>";
			if (pending) {
				html +=
					'<p class="ep-hint">' +
					"This one is still with HR for approval. Cancelling it removes the request.</p>";
			}
		}
		return html;
	}

	function withdrawClaim(name, button) {
		var label = button.innerHTML;
		button.disabled = true;
		button.innerHTML = '<span class="ep-spin"></span> Withdrawing';
		call({ action: "withdraw_expense_claim", token: state.token, name: name })
			.then(function () {
				toast(t("The claim has been withdrawn."), "success");
				show("expenses");
			})
			// `show` has already replaced the screen, so the restore below finds nothing
			// on the success path. It runs for the failure path, where `state.error`
			// re-renders the list and the button is gone with it - harmless either way,
			// and it stops a button being left spinning if the list ever keeps it.
			.catch(function (err) {
				if (expired(err && err.message)) return;
				state.error = (err && err.message) || "The claim could not be withdrawn.";
				show("expenses");
			})
			.then(function () {
				var again = document.getElementById("withdrawClaim");
				if (again) {
					again.disabled = false;
					again.innerHTML = label;
				}
			});
	}

	function renderExpenseForm() {
		host.innerHTML =
			'<div class="ep-fade">' +
			screenBar("formBack") +
			loadingPanel() +
			"</div>";
		document.getElementById("formBack").addEventListener("click", function () {
			show("expenses");
		});
		call({ action: "expense_claim_types", token: state.token })
			.then(function (d) {
				var offered = (d.expense_types || []).filter(function (row) {
					return row.usable;
				});
				var withheld = (d.expense_types || []).filter(function (row) {
					return !row.usable;
				});
				if (!offered.length) {
					document.getElementById("panelBody").innerHTML = emptyBlock(
						"fa-money",
						"No expense categories are available",
						"No category has an expense account for " + esc(d.company || "your company") + "."
					);
					return;
				}
				var options = offered
					.map(function (row) {
						return (
							'<option value="' +
							esc(row.name) +
							'">' +
							esc(row.label || row.name) +
							"</option>"
						);
					})
					.join("");
				document.getElementById("panelBody").innerHTML =
					'<p class="ep-card-title">Claim an expense</p>' +
					'<form id="claimForm" novalidate>' +
					'<div class="ep-field"><label class="ep-label" for="subject">What for</label>' +
					'<input class="ep-input" id="subject" placeholder="October expenses" autocomplete="off" /></div>' +
					'<div class="ep-field"><label class="ep-label" for="ct">Category</label>' +
					'<select class="ep-input" id="ct">' +
					options +
					"</select></div>" +
					'<div class="ep-field"><label class="ep-label" for="amt">Amount</label>' +
					'<input class="ep-input" id="amt" type="number" inputmode="decimal" step="0.01" min="0" required /></div>' +
					'<div class="ep-field"><label class="ep-label" for="when">Date</label>' +
					'<input class="ep-input" id="when" type="date" required /></div>' +
					'<div class="ep-field"><label class="ep-label" for="note">Note (optional)</label>' +
					'<input class="ep-input" id="note" autocomplete="off" /></div>' +
					'<button class="ep-btn" type="submit">Submit for approval</button></form>' +
					(withheld.length
						? '<p class="ep-hint">' +
						  esc(
								// One row with two holes rather than an English lead-in glued to
								// an English tail: the names and the company are the only parts
								// that should stay as they are, and a half-translated sentence
								// is worse to read than a short one.
								t(
									"Not shown: {0}. No expense account is set up for these on {1}.",
									[
										withheld
											.map(function (row) {
												return row.label || row.name;
											})
											.join(", "),
										d.company || t("your company"),
									]
								)
						  ) +
						  "</p>"
						: "");

				var form = document.getElementById("claimForm");
				form.addEventListener("submit", function (e) {
					e.preventDefault();
					submitClaim();
				});
			})
			.catch(function (err) {
				if (expired(err && err.message)) return;
				document.getElementById("panelBody").innerHTML = emptyBlock(
					"fa-exclamation-triangle",
					"Cannot load expense categories",
					(err && err.message) || ""
				);
			});
	}

	function submitClaim() {
		var amountField = document.getElementById("amt");
		var amount = amountField.value;
		if (!amount || Number(amount) <= 0) {
			// Reported in place rather than through `state.error`: that would re-enter
			// `render`, which re-fetches the categories and rebuilds the form, throwing
			// away everything the employee had already typed.
			amountField.setAttribute("aria-invalid", "true");
			var body = document.getElementById("panelBody");
			if (body && !body.querySelector(".ep-alert")) {
				body.innerHTML =
					'<div class="ep-alert ep-alert--bad"><i class="fa fa-exclamation-triangle"></i>' +
					"<span>Enter an amount more than zero.</span></div>" +
					body.innerHTML;
			}
			amountField.focus();
			return;
		}
		amountField.removeAttribute("aria-invalid");
		var button = document.querySelector("#claimForm button[type=submit]");
		button.disabled = true;
		button.innerHTML = '<span class="ep-spin"></span> Submitting';
		call({
			action: "file_expense_claim",
			token: state.token,
			title: document.getElementById("subject").value,
			posting_date: document.getElementById("when").value,
			items: [
				{
					expense_type: document.getElementById("ct").value,
					amount: Number(amount),
					expense_date: document.getElementById("when").value,
					description: document.getElementById("note").value,
				},
			],
		})
			.then(function () {
				// Says it is with HR, because a claim that sits unapproved for a week
				// reads exactly like one nobody received.
				toast(t("Submitted. It is now with HR for approval."), "success");
				show("expenses");
			})
			.catch(function (err) {
				if (expired(err && err.message)) return;
				var body = document.getElementById("panelBody");
				if (body) {
					body.innerHTML =
						'<div class="ep-alert ep-alert--bad"><i class="fa fa-exclamation-triangle"></i><span>' +
						esc(err.message) +
						"</span></div>" +
						body.innerHTML;
				}
				var again = document.querySelector("#claimForm button[type=submit]");
				if (again) {
					again.disabled = false;
					again.innerHTML = "Submit for approval";
				}
			});
	}

	function renderPayslipList(d) {
		var rows = d.payslips || [];
		if (!rows.length) {
			return emptyBlock("fa-file-pdf-o", "No payslips yet", "Your payslips appear once payroll has run.");
		}
		var html = '<p class="ep-card-title">Payslips</p>';
		rows.forEach(function (r) {
			html +=
				'<div class="ep-row ep-row--link" data-slip="' +
				esc(r.name) +
				'">' +
				'<div class="ep-row-main"><div class="ep-row-title">' +
				esc(dateOnly(r.start_date)) +
				" &rarr; " +
				esc(dateOnly(r.end_date)) +
				'</div><div class="ep-row-meta">' +
				esc(r.status || "") +
				'</div></div><div class="ep-row-value">' +
				money(r.net_pay, r.currency) +
				' <i class="fa fa-chevron-right ep-row-go"></i></div></div>';
		});
		return html;
	}

	// Bound by `renderPanel` *after* the html is in the document, not from here.
	// `renderPayslipList` runs to build a string, and the caller assigns that string
	// to `panelBody.innerHTML` afterwards - so anything queried inside it does not
	// exist yet and every listener goes onto nothing. The list rendered perfectly and
	// no row did anything when tapped.
	function bindPayslipRows() {
		var body = document.getElementById("panelBody");
		if (!body) return;
		Array.prototype.forEach.call(body.querySelectorAll("[data-slip]"), function (el) {
			el.addEventListener("click", function () {
				// `openPayslip` alone, with no `show()` first. There is no "payslip"
				// screen: a detail is always a panel rendered over the list. Calling
				// `show("payslip")` sends the router to its default case, which is the
				// sign-in form - so the panel is torn down and the detail then lands
				// in a `#panelBody` that no longer exists.
				openPayslip(el.dataset.slip);
			});
		});
	}

	function openPayslip(name) {
		var body = document.getElementById("panelBody");
		call({ action: "my_payslip", token: state.token, name: name })
			.then(function (d) {
				body.innerHTML = renderPayslipDetail(d);
				// Bound after the innerHTML is in the document - the button has to
				// exist before it can be given a handler. The slip's own name is used
				// rather than the one passed in, so the download can never be pointed
				// at a different payslip than the one on screen.
				bindDownloadButton(d.name);
			})
			.catch(function (err) {
				if (expired(err && err.message)) return;
				body.innerHTML = emptyBlock(
					"fa-exclamation-triangle",
					"Cannot open this payslip",
					(err && err.message) || ""
				);
			});
	}

	function renderPayslipDetail(d) {
		function table(heading, icon, rows) {
			if (!rows || !rows.length) return "";
			return (
				'<p class="ep-card-title ep-mt-9"><i class="fa ' +
				icon +
				'"></i> ' +
				esc(heading) +
				"</p>" +
				rows
					.map(function (r) {
						return (
							'<div class="ep-row ep-row--tight">' +
							'<div class="ep-row-main">' +
							esc(r.component) +
							'</div><div class="ep-row-value">' +
							money(r.amount, d.currency) +
							"</div></div>"
						);
					})
					.join("")
			);
		}
		return (
			'<button type="button" class="ep-btn ep-btn-ghost ep-mb-9" id="downloadPdf">' +
			'<i class="fa fa-download"></i> Download PDF</button>' +
			'<p class="ep-card-title">Payslip</p>' +
			'<div class="ep-quiet ep-quiet--block">' +
			esc(dateOnly(d.start_date)) +
			" &rarr; " +
			esc(dateOnly(d.end_date)) +
			'</div><span class="ep-badge ' +
			tone(d.status) +
			'">' +
			esc(d.status) +
			"</span>" +
			table("Earnings", "fa-arrow-circle-up", d.earnings) +
			table("Deductions", "fa-arrow-circle-down", d.deductions) +
			table("Employer side (not deducted)", "fa-building", d.employer) +
			'<div class="ep-row ep-row--total">' +
			'<div class="ep-row-main"><strong>Net pay</strong></div>' +
			'<div class="ep-row-value">' +
			money(d.net_pay, d.currency) +
			"</div></div>"
		);
	}

	// Base64 -> bytes -> a Blob the tablet's own PDF viewer opens.
	//
	// `atob` gives one character per byte, which mangles any multi-byte sequence, so
	// the string is expanded through `Uint8Array` rather than trusted. The PDF's
	// size is checked against what the server said it sent: a truncated download
	// still decodes to something that starts `%PDF-`, so without this the employee
	// would be shown a file that will not open, with no indication why.
	function base64ToBlob(b64, type) {
		var binary = atob(b64);
		var bytes = new Uint8Array(binary.length);
		for (var i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
		return new Blob([bytes], { type: type });
	}

	function downloadPayslipPdf(name, button) {
		var label = button.innerHTML;
		button.disabled = true;
		button.innerHTML = '<span class="ep-spin"></span> Preparing';
		call({ action: "payslip_pdf", token: state.token, name: name })
			.then(function (d) {
				var blob = base64ToBlob(d.pdf, "application/pdf");
				if (d.size && blob.size !== d.size) {
					throw new Error(
						"The download was incomplete (" + blob.size + " of " + d.size + " bytes)."
					);
				}
				var url = URL.createObjectURL(blob);
				var tab = window.open(url, "_blank");
				if (!tab) {
					// A wall tablet with a strict popup policy. Falling back to a
					// download means the employee gets the file either way.
					var link = document.createElement("a");
					link.href = url;
					link.download = d.filename;
					link.click();
					toast(t("The PDF has been downloaded."), "success");
				} else {
					toast(t("The PDF has been opened in a new tab."), "success");
				}
				// Revoked late rather than immediately: revoking it at once can
				// cancel the new tab's own load, leaving an empty page.
				window.setTimeout(function () {
					URL.revokeObjectURL(url);
				}, 60000);
			})
			.catch(function (err) {
				if (expired(err && err.message)) return;
				state.error = (err && err.message) || "The PDF could not be prepared.";
				render();
			})
			.then(function () {
				var again = document.getElementById("downloadPdf");
				if (again) {
					again.disabled = false;
					again.innerHTML = label;
				}
			});
	}

	function bindDownloadButton(name) {
		var button = document.getElementById("downloadPdf");
		if (!button) return;
		button.addEventListener("click", function () {
			downloadPayslipPdf(name, button);
		});
	}

	function alertHtml() {
		if (!state.error) return "";
		return (
			'<div class="ep-alert ep-alert--bad"><i class="fa fa-exclamation-triangle"></i><span>' +
			esc(state.error) +
			"</span></div>"
		);
	}

	// The same bar as a refusal, in the colour that says it was not one. A save that
	// answers only by redrawing the form looks like nothing happened.
	function infoAlert(text) {
		return (
			'<div class="ep-alert ep-alert--good"><i class="fa fa-check-circle"></i><span>' +
			esc(text) +
			"</span></div>"
		);
	}

	function emptyBlock(icon, title, sub) {
		return (
			'<div class="ep-empty"><div class="ep-empty-icon"><i class="fa ' +
			esc(icon) +
			'"></i></div><div class="ep-empty-title">' +
			esc(title) +
			"</div>" +
			(sub ? '<div class="ep-quiet">' + esc(sub) + "</div>" : "") +
			"</div>"
		);
	}

	// ---------------------------------------------------------- compensatory approvals



	// -------------------------------------------------------------- chrome
	function initLangMenu() {
		var btn = document.getElementById("langBtn");
		var dd = document.getElementById("langDd");
		if (!btn || !dd) return;

		function close() {
			dd.hidden = true;
			btn.setAttribute("aria-expanded", "false");
			btn.classList.remove("active");
		}

		btn.addEventListener("click", function (e) {
			e.stopPropagation();
			var open = dd.hidden;
			dd.hidden = !open;
			btn.classList.toggle("active", open);
			btn.setAttribute("aria-expanded", open ? "true" : "false");
		});
		document.addEventListener("click", function (e) {
			var wrap = document.getElementById("langWrap");
			if (wrap && !wrap.contains(e.target)) close();
		});
		Array.prototype.forEach.call(dd.querySelectorAll(".ep-lang-item"), function (el) {
			el.addEventListener("click", function () {
				var code = el.dataset.code;
				close();
				if (!code || code === CFG.lang) return;
				// The language is a server-side cookie, and this route is not cached:
				// offline the request can only fail, so say so without sending it.
				if (!navigator.onLine) {
					showOfflineBanner();
					return;
				}
				// The choice belongs to the device, not to whoever signed in last, so it
				// is a cookie and the page is reloaded rather than re-rendered: the
				// strings, the direction and the `lang` attribute all come from the
				// server render. The server sets the cookie, so a browser that blocks
				// script-written cookies still gets the right language.
				fetch(LANG_API, {
					method: "POST",
					credentials: "same-origin",
					headers: { "Content-Type": "application/json", "X-Frappe-CSRF-Token": csrf() },
					body: JSON.stringify({ lang: code }),
				})
					.then(function (response) {
						if (!response.ok) throw new Error("The language could not be changed.");
						window.location.reload();
					})
					.catch(function () {
						// The choice is a server-side cookie, so offline there is nothing
						// to apply and a reload would only fail. Say why instead.
						showOfflineBanner();
					});
			});
		});
	}

	// The light/dark switch. The CSS holds every colour as a variable under
	// `.ep-root.dark`, so this only moves a class - and the choice is kept in
	// localStorage rather than a cookie, because it belongs to whoever is holding the
	// tablet, not to the tablet.
	function initTheme() {
		var btn = document.getElementById("themeBtn");
		var icon = document.getElementById("themeIcon");
		if (!btn) return;

		var stored = null;
		try {
			stored = localStorage.getItem(THEME_KEY);
		} catch (e) {
			// A private-mode tablet refuses storage; the theme then just follows
			// the OS for this visit.
		}
		var prefersDark =
			window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches;
		var dark = stored ? stored === "dark" : !!prefersDark;

		function apply() {
			root.classList.toggle("dark", dark);
			btn.setAttribute("aria-pressed", dark ? "true" : "false");
			if (icon) icon.className = "fa " + (dark ? "fa-sun-o" : "fa-moon-o");
			// The installed app's status bar is painted before the page can style it,
			// so the only colour it can follow is this meta tag. Keep it on the header's
			// surface rather than a white strip above a dark screen.
			var meta = document.querySelector('meta[name="theme-color"]');
			if (meta) meta.setAttribute("content", dark ? "#171717" : "#f8f8f8");
		}
		apply();

		btn.addEventListener("click", function () {
			dark = !dark;
			apply();
			try {
				localStorage.setItem(THEME_KEY, dark ? "dark" : "light");
			} catch (e) {}
		});

		if (window.matchMedia) {
			var mql = window.matchMedia("(prefers-color-scheme: dark)");
			var onSystemChange = function (e) {
				var kept = null;
				try {
					kept = localStorage.getItem(THEME_KEY);
				} catch (err) {}
				// A choice the employee made beats the system: only an unset theme
				// follows the OS.
				if (kept) return;
				dark = e.matches;
				apply();
			};
			if (mql.addEventListener) mql.addEventListener("change", onSystemChange);
			else if (mql.addListener) mql.addListener(onSystemChange);
		}
	}

	// Every control that ends the session - the header's, and the one at the foot of
	// the sidebar - is found by attribute and answers to one handler, so the two can
	// not drift into different behaviours.
	function signOutControls() {
		return Array.prototype.slice.call(document.querySelectorAll("[data-signout]"));
	}

	function initSignOut() {
		signOutControls().forEach(function (btn) {
			btn.addEventListener("click", function () {
				var token = state.token;
				forgetToken();
				state.employee = null;
				state.directory = null;
				state.notifCounts = {};
				show("login");
				// The row is deleted server-side, not expired, so a tablet left signed in
				// at the reception desk cannot be handed on as-is.
				if (token) call({ action: "sign_out", token: token }).catch(function () {});
			});
		});
	}

	// ----------------------------------------------------------------- boot
	function boot() {
		// Before any screen is drawn: the first render is a mutation, and a watcher
		// installed after it would leave the login card in English until the employee
		// signed in and something redrew it.
		watchLanguage();
		initNav();
		initTheme();
		initLangMenu();
		initSignOut();
		initInstall();
		initPWA();
		initInactivity();
		initErrorReporting();
		restoreRemembered();
		askAuthenticator();

		var token = restoreToken();
		if (!token) {
			show("login");
			return;
		}
		// A remembered session that a finger was put in front of is not walked into on
		// the employee's behalf. The token is here, and it stays unread until the reader
		// agrees - which is the entire reason anyone would tick the box on a shared floor.
		if (state.credentialId) {
			// And a session an earlier visit left on the disk is taken back out rather
			// than kept as a second way in that nobody would know to guard. Numbers left
			// over from before this tablet was enrolled go the same way.
			clearRemembered();
			forgetIdentity();
			show("login");
			return;
		}
		// A stored token is not trusted on its own: the profile call is what proves it
		// is still live, and an expired one is discarded here rather than on the first
		// screen the employee opens.
		restoreSession(token);
	}

	// Walking back into a session that is still on the device, whether at boot or after
	// the inactivity lock. The read is the proof: a token alone is only a string, and the
	// one thing not done here is throwing it away because a single call failed - a tablet
	// whose wifi dipped for four seconds still has a live session on disk, and forgetting
	// it would turn a stumble into a hunt for the National ID.
	function restoreSession(token) {
		state.token = token;
		return call({ action: "my_profile", token: token })
			.then(function (d) {
				state.employee = d;
				show("dashboard");
			})
			.catch(function (err) {
				if (expired(err && err.message)) return;
				state.token = null;
				state.error = (err && err.message) || "Cannot reach the server.";
				render();
			});
	}

	// -------------------------------------------------------------- inactivity
	/* Thirty seconds with nobody touching the tablet, and the screen goes back to the door
	 * it came in through.
	 *
	 * A wall-mounted self-service is used by one person at a time and signed out by none
	 * of them: the employee who walks away to answer a colleague leaves their balances,
	 * their payslips and their shift on the screen for the next pair of hands. This is the
	 * legacy page's behaviour, ported because it is the one thing that makes leaving a
	 * tablet signed in safe at all.
	 *
	 * What the lock hides is the screen, not the session: a remembered one stays on the
	 * device, because "keep me signed in" is a promise about not typing the numbers again
	 * and taking it back over idle time would break it. A tablet that was given a
	 * fingerprint has nothing to keep, so its lock costs a touch.
	 */
	var INACTIVITY_MS = 30000;
	var inactivityTimer = null;

	function armInactivity() {
		if (state.screen === "login") return;
		if (inactivityTimer) clearTimeout(inactivityTimer);
		inactivityTimer = setTimeout(lockScreen, INACTIVITY_MS);
	}

	function lockScreen() {
		inactivityTimer = null;
		if (state.screen === "login") return;
		// In memory only, and no `sign_out` request: walking away is not an employee asking
		// to be signed out, and a POST that cannot be made offline would silently do
		// nothing while looking as though it had. The row expires on its own.
		state.token = null;
		state.employee = null;
		state.directory = null;
		state.notifCounts = {};
		show("login");
	}

	// The same event list the legacy page watched - every way a hand can say "I am still
	// here" that a wall tablet has. `passive` because this runs on every scroll and must
	// never be the reason one is late.
	function initInactivity() {
		["click", "mousedown", "keydown", "touchstart", "scroll"].forEach(function (event) {
			document.addEventListener(event, armInactivity, { passive: true });
		});
	}

	// ------------------------------------------------------------------ errors
	/* A wall tablet has no devtools. An exception that escaped used to present as "nothing
	 * works", with no clue as to why, and the only record of it was a console nobody was
	 * looking at. Whatever gets out is now put on the screen as well as into the log, in a
	 * box that can be dismissed - because the person standing in front of it is the only
	 * witness there is.
	 */
	function showFatal(title, detail) {
		var box = document.getElementById("epFatal");
		if (!box) {
			box = document.createElement("div");
			box.id = "epFatal";
			box.className = "ep-fatal";
			root.appendChild(box);
		}
		box.innerHTML =
			'<div class="ep-alert ep-alert--bad">' +
			'<i class="fa fa-exclamation-triangle"></i><span><strong>' +
			esc(title) +
			"</strong><br>" +
			'<span class="ep-fatal-detail">' +
			esc(detail || "") +
			"</span></span>" +
			'<button type="button" class="ep-fatal-x" aria-label="Dismiss">&times;</button></div>';
		var close = box.querySelector(".ep-fatal-x");
		if (close) close.addEventListener("click", function () { box.remove(); });
	}

	function initErrorReporting() {
		window.addEventListener("error", function (event) {
			showFatal(
				"Portal error",
				(event.message || "") +
					" (" +
					String(event.filename || "").split("/").pop() +
					":" +
					event.lineno +
					")"
			);
		});
		// The rejection a promise nobody attached a `catch` to. Every biometric and punch
		// path has one, and this is the net under the rest - a `TypeError` from a screen
		// that was edited last week should not need a laptop to notice.
		window.addEventListener("unhandledrejection", function (event) {
			var reason = event.reason || {};
			showFatal("Portal error", reason.message || String(reason));
		});
	}

	// Asked once at boot, because whether this tablet has a reader is a fact about the
	// hardware rather than about the visit. The answer arrives a moment later than the
	// first paint, so the login card is redrawn for it once - before anyone has had time
	// to type anything into it.
	function askAuthenticator() {
		platformAuthenticator().then(function (available) {
			state.bioReady = available;
			if (available && state.screen === "login" && !state.loading) render();
		});
	}

	// The browser keeps its own install entry inside a menu nobody on this floor will
	// find, so the offer is taken, held, and put beside the app's name instead. It is
	// answered by the tap on that button or not at all, which is precisely what the
	// banner that used to live here failed to do.
	function initInstall() {
		var button = document.getElementById("epInstallBtn");
		if (!button) return;

		var deferred = null;
		var label = '<i class="fa fa-download"></i><span>Install</span>';

		window.addEventListener("beforeinstallprompt", function (event) {
			event.preventDefault();
			deferred = event;
			button.hidden = false;
		});

		window.addEventListener("appinstalled", function () {
			deferred = null;
			button.hidden = true;
		});

		button.addEventListener("click", function () {
			if (!deferred) {
				button.hidden = true;
				return;
			}
			// `prompt()` returns a promise in current Chrome. Offline it can reject
			// because there is no install UI to show; without this catch that rejection
			// is an uncaught console error.
			var prompted = deferred.prompt();
			if (prompted && typeof prompted.catch === "function") {
				prompted.catch(function () {
					deferred = null;
				});
			}
			deferred.userChoice.then(
				function (choice) {
					deferred = null;
					button.hidden = !!(choice && choice.outcome === "accepted");
				},
				function () {
					deferred = null;
				}
			);
			button.innerHTML = '<span class="ep-spin"></span>';
			button.disabled = true;
			window.setTimeout(function () {
				button.disabled = false;
				button.innerHTML = label;
			}, 1200);
		});
	}

	function initPWA() {
		// Listen for SW messages (version updates)
		if ('serviceWorker' in navigator) {
			navigator.serviceWorker.addEventListener('message', function(event) {
				if (event.data && event.data.version) {
					console.log('SW version:', event.data.version);
				}
			});
		}

		// Check for app updates
		if ('serviceWorker' in navigator) {
			navigator.serviceWorker.ready.then(function(reg) {
				reg.addEventListener('updatefound', function() {
					var newWorker = reg.installing;
					if (newWorker) {
						newWorker.addEventListener('statechange', function() {
							if (newWorker.state === 'installed' && navigator.serviceWorker.controller) {
								// New version available, prompt reload
								// `t()` rather than the walker: a native dialog is drawn by
								// the browser, outside this document, so there is no text
								// node here to translate after the fact.
								if (
									confirm(
										t("A new version of the portal is available. Reload to update?")
									)
								) {
									window.location.reload();
								}
							}
						});
					}
				});
			});
		}
	}

	boot();
})();