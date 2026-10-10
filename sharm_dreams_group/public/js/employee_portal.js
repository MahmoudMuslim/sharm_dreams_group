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
	var LANG_COOKIE = CFG.langCookie || "employee_portal_lang";
	var TOKEN_KEY = "employee_portal.token";

	var host = document.getElementById("screenHost");
	var root = document.getElementById("epRoot");
	if (!host || !root) return;

	// ------------------------------------------------------------------ state
	var state = {
		screen: "login",
		token: null,
		employee: null,
		loading: false,
		error: "",
	};

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
			show("login");
			return true;
		}
		return false;
	}

	function call(payload) {
		state.loading = true;
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
					if (data.message && data.message.error) throw new Error(data.message.error);
					return data.message;
				});
			})
			.then(function (message) {
				state.loading = false;
				return message;
			})
			.catch(function (e) {
				state.loading = false;
				if (e && e.name === "TypeError") {
					throw new Error("Cannot reach the server. Check the network and try again.");
				}
				throw e;
			});
	}

	function fail(error) {
		// Cleared before render, because a refusal is the only thing that sets it and
		// a stale one would greet the next screen.
		if (expired(error && error.message)) return;
		state.error = (error && error.message) || "Something went wrong.";
		render();
	}

	function rememberToken(token) {
		state.token = token;
		try {
			window.sessionStorage.setItem(TOKEN_KEY, token);
		} catch (e) {
			/* private browsing: the session simply will not survive a reload */
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
	}

	function restoreToken() {
		try {
			return window.sessionStorage.getItem(TOKEN_KEY);
		} catch (e) {
			return null;
		}
	}

	// ---------------------------------------------------------------- screens
	function show(screen) {
		state.screen = screen;
		state.error = "";
		render();
	}

	function render() {
		switch (state.screen) {
			case "dashboard":
				return renderDashboard();
			case "balances":
				return renderBalances();
			case "time_off":
				return renderTimeOff();
			case "time_off_form":
				return renderTimeOffForm();
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
			case "compensatory_approvals":
				return renderCompensatoryApprovals();
			// No "payslip" screen: a slip is always opened through `openPayslip`, which
			// needs the slip's name. Routing it through `renderPanel` would post
			// `my_payslip` with no `name` and come back refused.
			default:
				return renderLogin();
		}
	}

	function renderLogin() {
		host.innerHTML =
			'<div class="ep-fade" style="max-width:24rem;margin:2rem auto">' +
			'<div class="ep-card">' +
			'<h1 style="margin:0 0 .3rem;font-size:1.3rem">Employee Portal</h1>' +
			'<p style="margin:0 0 1.1rem;color:var(--ep-muted);font-size:.95rem">' +
			"Sign in with your National ID and Employee Number.</p>" +
			alertHtml() +
			'<form id="loginForm" novalidate>' +
			'<div class="ep-field"><label class="ep-label" for="nid">National ID</label>' +
			'<input class="ep-input" id="nid" name="national_id" inputmode="numeric" autocomplete="off" required /></div>' +
			'<div class="ep-field"><label class="ep-label" for="code">Employee Number</label>' +
			'<input class="ep-input" id="code" name="employee_code" autocomplete="off" required /></div>' +
			'<button class="ep-btn" type="submit" id="loginBtn">' +
			(state.loading ? '<span class="ep-spin"></span> Signing in' : "Sign in") +
			"</button></form></div></div>";

		var form = document.getElementById("loginForm");
		form.addEventListener("submit", function (e) {
			e.preventDefault();
			signIn(document.getElementById("nid").value, document.getElementById("code").value);
		});
		var first = document.getElementById("nid");
		if (first) first.focus();
	}

	function signIn(nationalId, employeeCode) {
		state.loading = true;
		render();
		call({ action: "sign_in", national_id: nationalId, employee_code: employeeCode })
			.then(function (d) {
				rememberToken(d.token);
				state.employee = d;
				show("dashboard");
			})
			.catch(fail);
	}

	function renderDashboard() {
		var e = state.employee || {};
		host.innerHTML =
			'<div class="ep-fade">' +
			'<div class="ep-card"><h1 style="margin:0;font-size:1.25rem">' +
			esc(e.employee_name || "") +
			"</h1>" +
			'<p style="margin:.2rem 0 0;color:var(--ep-muted);font-size:.9rem">' +
			esc(e.designation || "") +
			(e.company ? " &middot; " + esc(e.company) : "") +
			"</p></div>" +
			alertHtml() +
			'<div class="ep-tiles">' +
			tile("balances", "fa-balance-scale", "Leave balances") +
			tile("time_off", "fa-calendar-check-o", "Time off") +
			tile("expenses", "fa-money", "Expenses") +
			tile("payslips", "fa-file-pdf-o", "My payslips") +
			tile("attendance", "fa-clock-o", "Shift & attendance") +
			tile("performance", "fa-line-chart", "Performance") +
			"</div></div>";
		bindTiles();
	}

	function tile(action, icon, label) {
		return (
			'<button type="button" class="ep-tile" data-tile="' +
			esc(action) +
			'"><span class="ep-tile-icon"><i class="fa ' +
			esc(icon) +
			'"></i></span><span>' +
			esc(label) +
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
	}

	function renderPanel(action, key, draw, bind) {
		host.innerHTML =
			'<div class="ep-fade">' +
			'<button type="button" class="ep-back" id="panelBack"><i class="fa fa-arrow-left"></i> Back</button>' +
			alertHtml() +
			'<div class="ep-card" id="panelBody"><div class="ep-empty"><span class="ep-spin"></span></div></div>' +
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
	function renderAttendance() {
		host.innerHTML =
			'<div class="ep-fade">' +
			'<button type="button" class="ep-back" id="panelBack"><i class="fa fa-arrow-left"></i> Back</button>' +
			alertHtml() +
			'<div class="ep-card" id="panelBody"><div class="ep-empty"><span class="ep-spin"></span></div></div>' +
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
					'<div class="ep-alert"><i class="fa fa-info-circle"></i><span>Outside your punch window (' +
					esc(day.check_in_from) +
					" &ndash; " +
					esc(day.check_in_until) +
					"). A tap now will be recorded for HR to review.</span></div>";
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
				html += '<div style="margin-top:.7rem">';
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

		html += '<p class="ep-card-title" style="margin-top:1rem">Last ' +
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

	function doPunch(button) {
		// Disabled for the round trip, not merely styled: the server collapses a
		// double-tap, but a button that stays live while the first request is in flight
		// invites the employee to wonder whether it worked.
		button.disabled = true;
		button.innerHTML = '<span class="ep-spin"></span> Recording';
		call({ action: "punch", token: state.token })
			.then(function (result) {
				// The screen is rebuilt from the server rather than patched from the
				// result: `punch` returns what it just did, but what the employee
				// should now see includes the day's hours, and only a re-read has those.
				state.error = result.offshift
					? "Recorded outside your shift. HR will review it."
					: "";
				renderAttendance();
			})
			.catch(function (err) {
				if (expired(err && err.message)) return;
				button.disabled = false;
				button.innerHTML = "Try again";
				state.error = (err && err.message) || "Could not record that.";
				render();
			});
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
		html += '<p class="ep-card-title" style="margin-top:1rem">My goals (' + goals.length + ")</p>";
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
						'<button type="button" class="ep-star" data-value="' +
						star +
						'"><i class="fa fa-star"></i></button>';
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
		if (readout) readout.textContent = value + " / 5";

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

	function renderBalances() {
		host.innerHTML =
			'<div class="ep-fade">' +
			'<button type="button" class="ep-back" id="panelBack"><i class="fa fa-arrow-left"></i> Back</button>' +
			alertHtml() +
			'<div class="ep-card" id="panelBody"><div class="ep-empty"><span class="ep-spin"></span></div></div>' +
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

	function balancesHtml(d) {
		var rows = d.balances || [];
		if (!rows.length) {
			return emptyBlock("fa-balance-scale", "No allocations yet", "Your leave balance will appear here once HR allocates it.");
		}
		var html =
			'<p class="ep-card-title">Leave balance &middot; ' + esc(d.year) + "</p>" +
			'<p style="margin:-.2rem 0 .8rem;color:var(--ep-muted);font-size:.9rem">' +
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
				'<div class="ep-row" style="border:0;padding:0">' +
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
			'<button type="button" class="ep-back" id="panelBack"><i class="fa fa-arrow-left"></i> Back</button>' +
			'<button type="button" class="ep-btn ep-btn-ghost" id="newRequest" style="margin-bottom:.8rem">' +
			'<i class="fa fa-plus"></i> Request time off</button>' +
			alertHtml() +
			'<div class="ep-card" id="panelBody"><div class="ep-empty"><span class="ep-spin"></span></div></div>' +
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
				"</span></div>";
		});
		return html;
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
			'<button type="button" class="ep-back" id="formBack"><i class="fa fa-arrow-left"></i> Back</button>' +
			'<div class="ep-card" id="panelBody"><div class="ep-empty"><span class="ep-spin"></span></div></div>' +
			"</div>";
		document.getElementById("formBack").addEventListener("click", function () {
			show("time_off");
		});
		call({ action: "leave_types", token: state.token })
			.then(function (d) {
				var offered = (d.leave_types || []).filter(function (t) {
					return t.has_allocation;
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
					.map(function (t) {
						return '<option value="' + esc(t.name) + '">' + esc(t.label || t.name) + "</option>";
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
				announce("Your request has been submitted.");
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
			'<button type="button" class="ep-btn ep-btn-ghost" id="newClaim" style="margin-bottom:.9rem">' +
			'<i class="fa fa-plus"></i> Claim an expense</button>';
		if (!rows.length) {
			return (
				html +
				'<div class="ep-empty"><div class="ep-empty-icon"><i class="fa fa-money"></i></div>' +
				'<div class="ep-empty-title">Nothing claimed yet</div>' +
				'<div style="font-size:.9rem">Anything you claim will be listed here.</div></div>'
			);
		}
		rows.forEach(function (r) {
			// The amount shown is the sanctioned one once there is one, because that is
			// what will actually be paid. Showing the claimed figure on an approved claim
			// would overstate it wherever HR sanctioned less.
			var amount = r.total_sanctioned_amount || r.total_claimed_amount;
			html +=
				'<div class="ep-row" data-claim="' +
				esc(r.name) +
				'" style="cursor:pointer">' +
				'<div class="ep-row-main"><div class="ep-row-title">' +
				esc(dateOnly(r.posting_date)) +
				'</div><div class="ep-row-meta">' +
				esc(claimLabel(r)) +
				'</div></div><div class="ep-row-value">' +
				money(amount, r.currency) +
				' <i class="fa fa-chevron-right" style="color:var(--ep-muted)"></i></div></div>';
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
			'<div style="color:var(--ep-muted);font-size:.9rem;margin-bottom:.6rem">' +
			esc(d.name) +
			" &middot; " +
			esc(dateOnly(d.posting_date)) +
			'</div><span class="ep-badge ' +
			claimTone(d) +
			'">' +
			esc(claimLabel(d)) +
			"</span>";

		html +=
			'<p class="ep-card-title" style="margin-top:.9rem"><i class="fa fa-list"></i> Lines</p>';
		if (!rows.length) {
			html +=
				'<div style="color:var(--ep-muted);font-size:.9rem">No lines recorded.</div>';
		}
		rows.forEach(function (r) {
			// Both figures once they differ. A sanctioned line that came back lower than
			// claimed is the single thing an employee most needs to see on this screen,
			// and it is invisible if only the sanctioned amount is shown.
			var differs =
				Number(r.sanctioned_amount || 0) > 0 &&
				Number(r.sanctioned_amount) !== Number(r.amount);
			html +=
				'<div class="ep-row" style="border:0;padding:.35rem 0">' +
				'<div class="ep-row-main"><div class="ep-row-title">' +
				esc(r.expense_type) +
				"</div>" +
				'<div class="ep-row-meta">' +
				esc(dateOnly(r.expense_date)) +
				(r.description ? " &middot; " + esc(r.description) : "") +
				(differs
					? " &middot; claimed " + money(r.amount, d.currency)
					: "") +
				"</div></div>" +
				'<div class="ep-row-value">' +
				money(differs ? r.sanctioned_amount : r.amount, d.currency) +
				"</div></div>";
		});

		if (d.receipts && d.receipts.length) {
			html +=
				'<p class="ep-card-title" style="margin-top:.9rem"><i class="fa fa-paperclip"></i> Receipts</p>';
			d.receipts.forEach(function (r) {
				html +=
					'<div class="ep-row" style="border:0;padding:.3rem 0"><div class="ep-row-main">' +
					esc(r.file_name) +
					"</div></div>";
			});
		}

		html +=
			'<div class="ep-row" style="margin-top:.9rem;border-top:1px solid var(--ep-line)">' +
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
				'<div class="ep-row" style="border:0;padding:.3rem 0">' +
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
				'<button type="button" class="ep-btn ep-btn-ghost" id="withdrawClaim" style="margin-top:1rem">' +
				'<i class="fa fa-' +
				(pending ? "times" : "trash-o") +
				'"></i> ' +
				(pending ? "Cancel this request" : "Withdraw this claim") +
				"</button>";
			if (pending) {
				html +=
					'<p style="margin-top:.5rem;color:var(--ep-muted);font-size:.85rem">' +
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
				announce("The claim has been withdrawn.");
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
			'<button type="button" class="ep-back" id="formBack"><i class="fa fa-arrow-left"></i> Back</button>' +
			'<div class="ep-card" id="panelBody"><div class="ep-empty"><span class="ep-spin"></span></div></div>' +
			"</div>";
		document.getElementById("formBack").addEventListener("click", function () {
			show("expenses");
		});
		call({ action: "expense_claim_types", token: state.token })
			.then(function (d) {
				var offered = (d.expense_types || []).filter(function (t) {
					return t.usable;
				});
				var withheld = (d.expense_types || []).filter(function (t) {
					return !t.usable;
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
					.map(function (t) {
						return (
							'<option value="' +
							esc(t.name) +
							'">' +
							esc(t.label || t.name) +
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
						? '<p style="margin-top:.9rem;color:var(--ep-muted);font-size:.85rem">Not shown: ' +
						  esc(
								withheld
									.map(function (t) {
										return t.label || t.name;
									})
									.join(", ")
						  ) +
						  " &mdash; no expense account is set up for these on " +
						  esc(d.company) +
						  ".</p>"
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
				announce("Submitted. It is now with HR for approval.");
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
				'<div class="ep-row" data-slip="' +
				esc(r.name) +
				'" style="cursor:pointer">' +
				'<div class="ep-row-main"><div class="ep-row-title">' +
				esc(dateOnly(r.start_date)) +
				" &rarr; " +
				esc(dateOnly(r.end_date)) +
				'</div><div class="ep-row-meta">' +
				esc(r.status || "") +
				'</div></div><div class="ep-row-value">' +
				money(r.net_pay, r.currency) +
				' <i class="fa fa-chevron-right" style="color:var(--ep-muted)"></i></div></div>';
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
				'<p class="ep-card-title" style="margin-top:.9rem"><i class="fa ' +
				icon +
				'"></i> ' +
				esc(heading) +
				"</p>" +
				rows
					.map(function (r) {
						return (
							'<div class="ep-row" style="border:0;padding:.35rem 0">' +
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
			'<button type="button" class="ep-btn ep-btn-ghost" id="downloadPdf" style="margin-bottom:.9rem">' +
			'<i class="fa fa-download"></i> Download PDF</button>' +
			'<p class="ep-card-title">Payslip</p>' +
			'<div style="color:var(--ep-muted);font-size:.9rem;margin-bottom:.6rem">' +
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
			'<div class="ep-row" style="margin-top:.9rem;border-top:1px solid var(--ep-line)">' +
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
					announce("The PDF has been downloaded.");
				} else {
					announce("The PDF has been opened in a new tab.");
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

	function emptyBlock(icon, title, sub) {
		return (
			'<div class="ep-empty"><div class="ep-empty-icon"><i class="fa ' +
			esc(icon) +
			'"></i></div><div class="ep-empty-title">' +
			esc(title) +
			"</div>" +
			(sub ? '<div style="font-size:.9rem">' + esc(sub) + "</div>" : "") +
			"</div>"
		);
	}

	// ---------------------------------------------------------- compensatory approvals
	function renderCompensatoryApprovals() {
		host.innerHTML =
			'<div class="ep-fade">' +
			'<button type="button" class="ep-back" id="panelBack"><i class="fa fa-arrow-left"></i> Back</button>' +
			alertHtml() +
			'<div class="ep-card" id="panelBody"><div class="ep-empty"><span class="ep-spin"></span></div></div>' +
			"</div>";
		document.getElementById("panelBack").addEventListener("click", function () {
			show("dashboard");
		});
		call({ action: "my_compensatory_proposals", token: state.token })
			.then(function (d) {
				document.getElementById("panelBody").innerHTML = compensatoryProposalsHtml(d);
				bindCompensatoryActions();
			})
			.catch(function (err) {
				if (expired(err && err.message)) return;
				document.getElementById("panelBody").innerHTML = emptyBlock(
					"fa-exclamation-triangle",
					"Cannot read proposals",
					(err && err.message) || ""
				);
			});
	}

	function compensatoryProposalsHtml(d) {
		var rows = d.proposals || [];
		if (!rows.length) {
			return emptyBlock("fa-check-circle", "No pending proposals", "All compensatory leave requests are up to date.");
		}
		var html =
			'<p class="ep-card-title">Compensatory Approvals</p>' +
			'<p style="margin:-.2rem 0 .8rem;color:var(--ep-muted);font-size:.9rem">' +
			esc(rows.length) +
			" proposal" +
			(rows.length === 1 ? "" : "s") +
			" awaiting review</p>";
		rows.forEach(function (p) {
			var created = longDate(p.creation);
			html +=
				'<div class="ep-balance">' +
				'<div class="ep-row" style="border:0;padding:0">' +
				'<div class="ep-row-main"><div class="ep-row-title">' +
				esc(p.employee_name || p.employee) +
				"</div>" +
				'<div class="ep-row-meta">' +
				esc(p.company || "") +
				(p.department ? " &middot; " + esc(p.department) : "") +
				" &middot; " +
				esc(created) +
				" &middot; " +
				esc(p.leave_type || "Leave In Lieu") +
				" &middot; " +
				esc(p.new_leaves_allocated) +
				" days" +
				"</div></div>" +
				'<div class="ep-row-value"><span class="ep-badge ep-badge--warn">' +
				"Pending" +
				"</span></div></div>" +
				'<div class="ep-reqs">' +
				esc(p.description || "") +
				"</div>" +
				'<div class="ep-actions" style="margin-top:.75rem;display:flex;gap:.5rem">' +
				'<button type="button" class="ep-btn ep-btn--good" data-action="approve" data-name="' +
				esc(p.name) +
				'"><i class="fa fa-check"></i> Approve</button>' +
				'<button type="button" class="ep-btn ep-btn-ghost" data-action="reject" data-name="' +
				esc(p.name) +
				'"><i class="fa fa-times"></i> Reject</button>' +
				"</div></div>";
		});
		return html;
	}

	function bindCompensatoryActions() {
		document.querySelectorAll("[data-action=approve]").forEach(function (btn) {
			btn.addEventListener("click", function () {
				var name = btn.dataset.name;
				if (!confirm("Approve this Leave In Lieu proposal?")) return;
				btn.disabled = true;
				btn.innerHTML = '<i class="fa fa-spin fa-spinner"></i> Approving...';
				call({ action: "approve_compensatory_proposal", token: state.token, name: name })
					.then(function () {
						renderCompensatoryApprovals();
					})
					.catch(function (err) {
						if (expired(err && err.message)) return;
						btn.disabled = false;
						btn.innerHTML = '<i class="fa fa-check"></i> Approve';
						announce(err && err.message ? err.message : "Could not approve.");
					});
			});
		});
		document.querySelectorAll("[data-action=reject]").forEach(function (btn) {
			btn.addEventListener("click", function () {
				var name = btn.dataset.name;
				var reason = prompt("Reason for rejection (optional):");
				if (reason === null) return; // user cancelled
				btn.disabled = true;
				btn.innerHTML = '<i class="fa fa-spin fa-spinner"></i> Rejecting...';
				call({ action: "reject_compensatory_proposal", token: state.token, name: name, reason: reason || "" })
					.then(function () {
						renderCompensatoryApprovals();
					})
					.catch(function (err) {
						if (expired(err && err.message)) return;
						btn.disabled = false;
						btn.innerHTML = '<i class="fa fa-times"></i> Reject';
						announce(err && err.message ? err.message : "Could not reject.");
					});
			});
		});
	}

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
				// The choice belongs to the device, not to whoever signed in last, so
				// it is a cookie and the page is reloaded rather than re-rendered.
				document.cookie =
					LANG_COOKIE + "=" + encodeURIComponent(code) + ";path=/;max-age=31536000";
				window.location.reload();
			});
		});
	}

	function initSignOut() {
		var btn = document.getElementById("signOutBtn");
		if (!btn) return;
		btn.addEventListener("click", function () {
			var token = state.token;
			forgetToken();
			show("login");
			if (token) call({ action: "sign_out", token: token }).catch(function () {});
		});
	}

	// ----------------------------------------------------------------- boot
	function boot() {
		initLangMenu();
		initSignOut();

		var token = restoreToken();
		if (!token) {
			render();
			return;
		}
		// A stored token is not trusted on its own: the profile call is what proves it
		// is still live, and an expired one is discarded here rather than on the first
		// screen the employee opens.
		state.token = token;
		call({ action: "my_profile", token: token })
			.then(function (d) {
				state.employee = {
					employee: d.employee,
					employee_name: d.employee_name,
					designation: d.designation,
					company: d.company,
				};
				show("dashboard");
			})
			.catch(function () {
				forgetToken();
				render();
			});
	}

	boot();
})();