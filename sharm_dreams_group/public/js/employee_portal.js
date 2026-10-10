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
			case "documents":
				return renderDocuments();
			case "announcements":
				return renderAnnouncements();
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
			case "shift_swaps":
				return renderShiftSwaps();
			case "shift_swap_form":
				return renderShiftSwapForm();
			case "benefits":
				return renderBenefits();
			case "learning":
				return renderLearning();
			case "assets":
				return renderAssets();
			case "asset_form":
				return renderAssetForm();
			case "travel":
				return renderTravel();
			case "travel_form":
				return renderTravelForm();
			case "loans":
				return renderLoans();
			case "loan_form":
				return renderLoanForm();
			case "wellness":
				return renderWellness();
			case "recognition":
				return renderRecognition();
			case "recognition_form":
				return renderRecognitionForm();
			case "support":
				return renderSupport();
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
		call({ action: "my_notifications", token: state.token })
			.then(function (d) {
				renderDashboardWithNotifications(e, d.items || []);
			})
			.catch(function () {
				renderDashboardWithNotifications(e, []);
			});
	}

	function renderDashboardWithNotifications(e, notifications) {
		var counts = countNotificationsByScreen(notifications);
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
			(notifications.length ? renderNotificationStrip(notifications) : "") +
			'<div class="ep-tiles">' +
			tile("balances", "fa-balance-scale", "Leave balances", counts.balances) +
			tile("time_off", "fa-calendar-check-o", "Time off", counts.time_off) +
			tile("recognition", "fa-trophy", "Recognition", counts.recognition) +
			tile("wellness", "fa-heartbeat", "Wellness & Safety", counts.wellness) +
			tile("loans", "fa-money", "Loans & Advances", counts.loans) +
			tile("travel", "fa-plane", "Travel", counts.travel) +
			tile("assets", "fa-laptop", "My Assets", counts.assets) +
			tile("learning", "fa-graduation-cap", "Learning", counts.learning) +
			tile("shift_swaps", "fa-exchange", "Shift Swaps", counts.shift_swaps) +
			tile("timesheets", "fa-clock-o", "Timesheets", counts.timesheets) +
			tile("overtime", "fa-bolt", "Overtime", counts.overtime) +
			tile("leave_calendar", "fa-calendar", "Team Calendar", counts.leave_calendar) +
			tile("org_chart", "fa-sitemap", "Org Chart", counts.org_chart) +
			tile("colleagues", "fa-users", "Colleagues", counts.colleagues) +
			tile("announcements", "fa-bullhorn", "Announcements", counts.announcements) +
			tile("benefits", "fa-heartbeat", "Benefits", counts.benefits) +
			tile("support", "fa-life-ring", "Help & Support", counts.support) +
			tile("documents", "fa-folder-open", "Documents", counts.documents) +
			tile("expenses", "fa-money", "Expenses", counts.expenses) +
			tile("payslips", "fa-file-pdf-o", "My payslips", counts.payslips) +
			tile("attendance", "fa-clock-o", "Shift & attendance", counts.attendance) +
			tile("performance", "fa-line-chart", "Performance", counts.performance) +
			"</div></div>";
		bindTiles();
	}

	function countNotificationsByScreen(items) {
		var counts = { balances: 0, time_off: 0, expenses: 0, payslips: 0, attendance: 0, performance: 0, documents: 0, announcements: 0, colleagues: 0, support: 0, org_chart: 0, timesheets: 0, overtime: 0, leave_calendar: 0, shift_swaps: 0, benefits: 0, learning: 0, assets: 0, travel: 0, loans: 0, wellness: 0, recognition: 0 };
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
		var badgeHtml = badge && badge > 0
			? '<span class="ep-tile-badge" style="background:var(--ep-' + (badge > 5 ? 'bad' : 'warn') + ');color:var(--ep-accent-ink);font-size:.7rem;font-weight:700;padding:.1rem .4rem;border-radius:999px;min-width:1.4rem;text-align:center;margin-left:.5rem">' +
			  esc(badge > 9 ? "9+" : badge) +
			  "</span>"
			: "";
		return (
			'<button type="button" class="ep-tile" data-tile="' +
			esc(action) +
			'"><span class="ep-tile-icon"><i class="fa ' +
			esc(icon) +
			'"></i></span><span style="display:flex;align-items:center;justify-content:space-between;width:100%">' +
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
		Array.prototype.forEach.call(host2.querySelectorAll(".ep-attn[data-screen]"), function (el) {
			el.addEventListener("click", function () {
				show(el.dataset.screen);
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

	function renderDocuments() {
		host.innerHTML =
			'<div class="ep-fade">' +
			'<button type="button" class="ep-back" id="panelBack"><i class="fa fa-arrow-left"></i> Back</button>' +
			alertHtml() +
			'<div class="ep-card" id="panelBody"><div class="ep-empty"><span class="ep-spin"></span></div></div>' +
			"</div>";
		document.getElementById("panelBack").addEventListener("click", function () {
			show("dashboard");
		});
		call({ action: "my_documents", token: state.token })
			.then(function (d) {
				document.getElementById("panelBody").innerHTML = documentsHtml(d);
			})
			.catch(function (err) {
				if (expired(err && err.message)) return;
				document.getElementById("panelBody").innerHTML = emptyBlock(
					"fa-exclamation-triangle",
					"Cannot read your documents",
					(err && err.message) || ""
				);
			});
	}

	function documentsHtml(d) {
		var rows = d.documents || [];
		var summary = d.summary || {};
		if (!rows.length) {
			return emptyBlock("fa-folder-open", "No documents recorded", "Your document checklist will appear here once HR adds it.");
		}
		var html =
			'<p class="ep-card-title">Employee File Index</p>' +
			'<div class="ep-doc-summary" style="display:flex;gap:1rem;flex-wrap:wrap;margin-bottom:1rem">' +
			'<span class="ep-badge ep-badge--good" style="font-size:.9rem;padding:.4rem .7rem"><i class="fa fa-check"></i> ' + esc(summary.held || 0) + ' Held</span>' +
			'<span class="ep-badge ep-badge--bad" style="font-size:.9rem;padding:.4rem .7rem"><i class="fa fa-times"></i> ' + esc(summary.missing || 0) + ' Missing</span>' +
			'<span class="ep-badge" style="font-size:.9rem;padding:.4rem .7rem;background:color-mix(in srgb, var(--ep-muted) 20%, transparent);color:var(--ep-muted)"><i class="fa fa-ban"></i> ' + esc(summary.not_applicable || 0) + ' N/A</span>' +
			'<span class="ep-badge" style="font-size:.9rem;padding:.4rem .7rem;background:color-mix(in srgb, var(--ep-muted) 20%, transparent);color:var(--ep-muted)"><i class="fa fa-question"></i> ' + esc(summary.not_recorded || 0) + ' Not Recorded</span>' +
			"</div>";
		rows.forEach(function (r) {
			var status = String(r.status || "").toLowerCase();
			var badgeClass = status === "held" ? "ep-badge--good" : status === "missing" ? "ep-badge--bad" : "ep-badge--info";
			var icon = status === "held" ? "fa-check-circle" : status === "missing" ? "fa-times-circle" : "fa-info-circle";
			html +=
				'<div class="ep-row" style="border-bottom:1px solid var(--ep-line);padding:.75rem 0">' +
				'<div class="ep-row-main" style="min-width:0">' +
				'<div class="ep-row-title" style="display:flex;align-items:center;gap:.5rem">' +
				'<i class="fa ' + esc(icon) + '" style="color:var(--ep-muted)"></i>' +
				esc(r.document) +
				"</div>" +
				(r.notes ? '<div class="ep-row-meta">' + esc(r.notes) + "</div>" : "") +
				"</div>" +
				'<span class="ep-badge ' + badgeClass + '">' + esc(r.status) + "</span>" +
				"</div>";
		});
		return html;
	}

	function renderAnnouncements() {
		host.innerHTML =
			'<div class="ep-fade">' +
			'<button type="button" class="ep-back" id="panelBack"><i class="fa fa-arrow-left"></i> Back</button>' +
			alertHtml() +
			'<div class="ep-card" id="panelBody"><div class="ep-empty"><span class="ep-spin"></span></div></div>' +
			"</div>";
		document.getElementById("panelBack").addEventListener("click", function () {
			show("dashboard");
		});
		call({ action: "my_announcements", token: state.token })
			.then(function (d) {
				document.getElementById("panelBody").innerHTML = announcementsHtml(d);
			})
			.catch(function (err) {
				if (expired(err && err.message)) return;
				document.getElementById("panelBody").innerHTML = emptyBlock(
					"fa-exclamation-triangle",
					"Cannot read announcements",
					(err && err.message) || ""
				);
			});
	}

	function announcementsHtml(d) {
		var rows = d.announcements || [];
		if (!rows.length) {
			return emptyBlock("fa-bullhorn", "No announcements", "Company announcements will appear here when published.");
		}
		var html = '<p class="ep-card-title">Announcements</p>';
		rows.forEach(function (r) {
			var priority = String(r.priority || "Normal").toLowerCase();
			var badgeClass = priority === "urgent" ? "ep-badge--bad" : priority === "high" ? "ep-badge--warn" : "ep-badge--info";
			var icon = priority === "urgent" ? "fa-exclamation-circle" : priority === "high" ? "fa-bolt" : "fa-bullhorn";
			var published = dateOnly(r.published_on);
			var expires = r.expires_on ? " &middot; Expires " + esc(dateOnly(r.expires_on)) : "";
			html +=
				'<div class="ep-announcement" style="border-bottom:1px solid var(--ep-line);padding:1rem 0">' +
				'<div style="display:flex;align-items:flex-start;gap:.75rem">' +
				'<span class="ep-attn-icon ep-attn-icon--' + badgeClass.replace("ep-badge--", "") + '" style="width:2.5rem;height:2.5rem;line-height:2.5rem;font-size:1.1rem;flex-shrink:0">' +
				'<i class="fa ' + esc(icon) + '"></i></span>' +
				'<div style="flex:1;min-width:0">' +
				'<div style="display:flex;align-items:center;gap:.5rem;flex-wrap:wrap;margin-bottom:.4rem">' +
				'<strong style="font-size:1.05rem">' + esc(r.title) + '</strong>' +
				'<span class="ep-badge ' + badgeClass + '">' + esc(r.priority) + '</span>' +
				'</div>' +
				'<div style="color:var(--ep-muted);font-size:.85rem;margin-bottom:.5rem">' +
				'Published ' + esc(published) + expires +
				'</div>' +
				'<div style="line-height:1.6">' + r.content + '</div>' +
				'</div>' +
				'</div>' +
				'</div>';
		});
		return html;
	}

	function renderColleagues() {
		host.innerHTML =
			'<div class="ep-fade">' +
			'<button type="button" class="ep-back" id="panelBack"><i class="fa fa-arrow-left"></i> Back</button>' +
			alertHtml() +
			'<div class="ep-card" id="panelBody"><div class="ep-empty"><span class="ep-spin"></span></div></div>' +
			"</div>";
		document.getElementById("panelBack").addEventListener("click", function () {
			show("dashboard");
		});
		call({ action: "my_colleagues", token: state.token })
			.then(function (d) {
				document.getElementById("panelBody").innerHTML = colleaguesHtml(d);
				bindColleaguesSearch(d);
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

	function colleaguesHtml(d) {
		var rows = d.colleagues || [];
		var departments = d.departments || [];
		var deptOptions = departments.map(function (dept) {
			return '<option value="' + esc(dept) + '">' + esc(dept) + '</option>';
		}).join('');
		if (!rows.length) {
			return emptyBlock("fa-users", "No colleagues found", "No active employees in your company.");
		}
		var html =
			'<p class="ep-card-title">Colleagues <span style="font-weight:400;color:var(--ep-muted);font-size:.9rem">(' + rows.length + ')</span></p>' +
			'<div class="ep-colleagues-toolbar" style="display:flex;gap:.75rem;flex-wrap:wrap;margin-bottom:1rem;align-items:center">' +
			'<div class="ep-field" style="flex:1;min-width:180px;margin-bottom:0">' +
			'<label class="ep-label" for="colleagueSearch" style="margin-bottom:.25rem">Search</label>' +
			'<input class="ep-input" id="colleagueSearch" type="search" placeholder="Name, number, designation..." inputmode="text" />' +
			'</div>' +
			'<div class="ep-field" style="min-width:160px;margin-bottom:0">' +
			'<label class="ep-label" for="colleagueDept" style="margin-bottom:.25rem">Department</label>' +
			'<select class="ep-input" id="colleagueDept">' +
			'<option value="">All departments</option>' + deptOptions +
			'</select>' +
			'</div>' +
			'</div>' +
			'<div id="colleaguesList">' + renderColleagueList(rows) + '</div>';
		return html;
	}

	function renderColleagueList(rows) {
		if (!rows.length) {
			return '<div class="ep-empty" style="padding:2rem"><i class="fa fa-users ep-empty-icon"></i><div class="ep-empty-title">No colleagues match</div><div style="font-size:.9rem">Try a different search or department.</div></div>';
		}
		var html = '<div class="ep-colleagues-grid">';
		rows.forEach(function (c) {
			var initials = (c.employee_name || "?").split(" ").map(function (w) { return w[0]; }).join("").substring(0, 2).toUpperCase();
			var avatar = c.image
				? '<img src="' + esc(c.image) + '" alt="" style="width:100%;height:100%;object-fit:cover;border-radius:50%">'
				: '<div style="width:100%;height:100%;display:flex;align-items:center;justify-content:center;border-radius:50%;background:linear-gradient(135deg,var(--ep-accent),var(--ep-accent-dark));color:var(--ep-accent-ink);font-weight:700;font-size:1.1rem">' + esc(initials) + '</div>';
			var dept = c.department ? '<div class="ep-row-meta"><i class="fa fa-building-o"></i> ' + esc(c.department) + '</div>' : '';
			var desig = c.designation ? '<div class="ep-row-meta"><i class="fa fa-briefcase"></i> ' + esc(c.designation) + '</div>' : '';
			var empNum = c.employee_number ? '<div class="ep-row-meta"><i class="fa fa-id-badge"></i> ' + esc(c.employee_number) + '</div>' : '';
			var phone = c.cell_number ? '<div class="ep-row-meta"><i class="fa fa-phone"></i> ' + esc(c.cell_number) + '</div>' : '';
			var email = c.company_email ? '<div class="ep-row-meta"><i class="fa fa-envelope-o"></i> ' + esc(c.company_email) + '</div>' : '';
			html +=
				'<div class="ep-colleague-card" style="border:1px solid var(--ep-line);border-radius:.8rem;padding:1rem;background:var(--ep-card)">' +
				'<div style="display:flex;gap:1rem;align-items:flex-start">' +
				'<div style="width:4rem;height:4rem;border-radius:50%;overflow:hidden;flex-shrink:0">' + avatar + '</div>' +
				'<div style="flex:1;min-width:0">' +
				'<div class="ep-row-title" style="font-size:1rem">' + esc(c.employee_name) + '</div>' +
				desig + dept + empNum + phone + email +
				'</div>' +
				'</div>' +
				'</div>';
		});
		html += '</div>';
		return html;
	}

	function bindColleaguesSearch(d) {
		var input = document.getElementById("colleagueSearch");
		var select = document.getElementById("colleagueDept");
		var list = document.getElementById("colleaguesList");
		if (!input || !select || !list) return;

		var debounceTimer;
		function doSearch() {
			var search = input.value.trim();
			var dept = select.value;
			call({ action: "my_colleagues", token: state.token, search: search || undefined, department: dept || undefined })
				.then(function (result) {
					list.innerHTML = renderColleagueList(result.colleagues || []);
				})
				.catch(function (err) {
					if (expired(err && err.message)) return;
					list.innerHTML = emptyBlock("fa-exclamation-triangle", "Search failed", (err && err.message) || "");
				});
		}

		input.addEventListener("input", function () {
			clearTimeout(debounceTimer);
			debounceTimer = setTimeout(doSearch, 300);
		});
		select.addEventListener("change", doSearch);
	}

	function renderOrgChart() {
		host.innerHTML =
			'<div class="ep-fade">' +
			'<button type="button" class="ep-back" id="panelBack"><i class="fa fa-arrow-left"></i> Back</button>' +
			'<div class="ep-org-toolbar" style="display:flex;gap:.75rem;flex-wrap:wrap;margin-bottom:1rem;align-items:center">' +
			'<button type="button" class="ep-btn ep-btn-ghost" id="orgExpandAll"><i class="fa fa-expand"></i> Expand All</button>' +
			'<button type="button" class="ep-btn ep-btn-ghost" id="orgCollapseAll"><i class="fa fa-compress"></i> Collapse All</button>' +
			'<button type="button" class="ep-btn ep-btn-ghost" id="orgFindMe"><i class="fa fa-crosshairs"></i> Find Me</button>' +
			'<div style="flex:1"></div>' +
			'<span id="orgStats" style="color:var(--ep-muted);font-size:.85rem"></span>' +
			'</div>' +
			alertHtml() +
			'<div class="ep-card" id="panelBody" style="overflow:auto"><div class="ep-empty"><span class="ep-spin"></span></div></div>' +
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
		tree.forEach(function (root) {
			html += renderOrgNode(root, 0, true);
		});
		html += '</div>';
		return html;
	}

	function renderOrgNode(node, depth, isRoot) {
		var initials = (node.employee_name || "?").split(" ").map(function (w) { return w[0]; }).join("").substring(0, 2).toUpperCase();
		var avatar = node.image
			? '<img src="' + esc(node.image) + '" alt="" style="width:100%;height:100%;object-fit:cover;border-radius:50%">'
			: '<div style="width:100%;height:100%;display:flex;align-items:center;justify-content:center;border-radius:50%;background:linear-gradient(135deg,var(--ep-accent),var(--ep-accent-dark));color:var(--ep-accent-ink);font-weight:700;font-size:.85rem">' + esc(initials) + '</div>';
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
			'<button type="button" class="ep-back" id="panelBack"><i class="fa fa-arrow-left"></i> Back</button>' +
			'<button type="button" class="ep-btn ep-btn-ghost" id="newTimesheet" style="margin-bottom:.8rem;float:right"><i class="fa fa-plus"></i> Log Time</button>' +
			alertHtml() +
			'<div class="ep-card" id="panelBody"><div class="ep-empty"><span class="ep-spin"></span></div></div>' +
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
				'<div class="ep-row" style="border-bottom:1px solid var(--ep-line);padding:.75rem 0">' +
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
			'<button type="button" class="ep-back" id="formBack"><i class="fa fa-arrow-left"></i> Back</button>' +
			'<div class="ep-card" id="panelBody"><div class="ep-empty"><span class="ep-spin"></span></div></div>' +
			"</div>";
		document.getElementById("formBack").addEventListener("click", function () {
			show("timesheets");
		});
		call({ action: "activity_types", token: state.token })
			.then(function (d) {
				var types = (d.activity_types || []).map(function (t) {
					return '<option value="' + esc(t.name) + '">' + esc(t.activity_type_name || t.name) + '</option>';
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
				announce("Timesheet submitted.");
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
			'<button type="button" class="ep-back" id="panelBack"><i class="fa fa-arrow-left"></i> Back</button>' +
			'<button type="button" class="ep-btn ep-btn-ghost" id="newOvertime" style="margin-bottom:.8rem;float:right"><i class="fa fa-plus"></i> Request Overtime</button>' +
			alertHtml() +
			'<div class="ep-card" id="panelBody"><div class="ep-empty"><span class="ep-spin"></span></div></div>' +
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
				'<div class="ep-row" style="border-bottom:1px solid var(--ep-line);padding:.75rem 0">' +
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
			'<button type="button" class="ep-back" id="formBack"><i class="fa fa-arrow-left"></i> Back</button>' +
			'<div class="ep-card" id="panelBody"><div class="ep-empty"><span class="ep-spin"></span></div></div>' +
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
				announce("Overtime request submitted.");
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
			'<button type="button" class="ep-back" id="panelBack"><i class="fa fa-arrow-left"></i> Back</button>' +
			'<div class="ep-other-toolbar" style="display:flex;gap:.75rem;flex-wrap:wrap;margin-bottom:1rem;align-items:center">' +
			'<label class="ep-label" style="margin-bottom:0"><i class="fa fa-calendar"></i> Month</label>' +
			'<select class="ep-input" id="calMonth" style="width:auto;min-width:140px"></select>' +
			'<label class="ep-label" style="margin-bottom:0"><i class="fa fa-calendar-o"></i> Year</label>' +
			'<select class="ep-input" id="calYear" style="width:auto;min-width:100px"></select>' +
			'<div style="flex:1"></div>' +
			'<span id="calStats" style="color:var(--ep-muted);font-size:.85rem"></span>' +
			'</div>' +
			alertHtml() +
			'<div class="ep-card" id="panelBody" style="overflow:auto"><div class="ep-empty"><span class="ep-spin"></span></div></div>' +
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
		var html = '<div class="ep-cal-grid">';
		var cur = new Date(monthStart);
		while (cur <= monthEnd) {
			var key = cur.toISOString().split("T")[0];
			var dayLeaves = byDate[key] || [];
			var isWeekend = cur.getDay() === 5 || cur.getDay() === 6; // Fri/Sat
			var isToday = key === dateOnly(frappe.datetime.nowdate ? frappe.datetime.nowdate() : new Date().toISOString().split("T")[0]);
			html +=
				'<div class="ep-cal-day' + (isWeekend ? " ep-cal-weekend" : "") + (isToday ? " ep-cal-today" : "") + (dayLeaves.length ? " ep-cal-has-leave" : "") + '" style="border:1px solid var(--ep-line);border-radius:.5rem;background:var(--ep-card);min-height:80px">' +
				'<div class="ep-cal-date" style="padding:.35rem .5rem;font-weight:600;text-align:center' + (isToday ? ";background:var(--ep-accent);color:var(--ep-accent-ink)" : "") + '">' + cur.getDate() + '</div>';
			dayLeaves.forEach(function (lv) {
				html +=
					'<div class="ep-cal-leave" style="padding:0 .4rem .35rem;font-size:.7rem;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">' +
					esc(lv.employee_name) + ": " + esc(lv.leave_type) +
					"</div>";
			});
			html += '</div>';
			cur = add_days(cur, 1);
		}
		html += '</div>';
		return html;
	}

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

	function renderShiftSwaps() {
		host.innerHTML =
			'<div class="ep-fade">' +
			'<button type="button" class="ep-back" id="panelBack"><i class="fa fa-arrow-left"></i> Back</button>' +
			'<button type="button" class="ep-btn ep-btn-ghost" id="newSwap" style="margin-bottom:.8rem;float:right"><i class="fa fa-plus"></i> Request Swap</button>' +
			alertHtml() +
			'<div class="ep-card" id="panelBody"><div class="ep-empty"><span class="ep-spin"></span></div></div>' +
			"</div>";
		document.getElementById("panelBack").addEventListener("click", function () {
			show("dashboard");
		});
		document.getElementById("newSwap").addEventListener("click", function () {
			show("shift_swap_form");
		});
		call({ action: "my_shift_swaps", token: state.token })
			.then(function (d) {
				document.getElementById("panelBody").innerHTML = shiftSwapsHtml(d);
				bindShiftSwapActions();
			})
			.catch(function (err) {
				if (expired(err && err.message)) return;
				document.getElementById("panelBody").innerHTML = emptyBlock(
					"fa-exclamation-triangle",
					"Cannot read shift swaps",
					(err && err.message) || ""
				);
			});
	}

	function bindShiftSwapActions() {
		var body = document.getElementById("panelBody");
		if (!body) return;
		
		body.querySelectorAll(".swap-approve").forEach(function (btn) {
			btn.addEventListener("click", function () {
				approveShiftSwap(btn.dataset.name, btn);
			});
		});
		
		body.querySelectorAll(".swap-reject").forEach(function (btn) {
			btn.addEventListener("click", function () {
				var reason = prompt("Rejection reason (optional):");
				rejectShiftSwap(btn.dataset.name, reason, btn);
			});
		});
	}

	function approveShiftSwap(name, button) {
		var label = button.innerHTML;
		button.disabled = true;
		button.innerHTML = '<span class="ep-spin"></span> Approving';
		call({ action: "approve_shift_swap", token: state.token, name: name })
			.then(function () {
				announce("Shift swap approved.");
				show("shift_swaps");
			})
			.catch(function (err) {
				if (expired(err && err.message)) return;
				button.disabled = false;
				button.innerHTML = label;
				state.error = (err && err.message) || "Could not approve.";
				render();
			});
	}

	function rejectShiftSwap(name, reason, button) {
		var label = button.innerHTML;
		button.disabled = true;
		button.innerHTML = '<span class="ep-spin"></span> Rejecting';
		call({ action: "reject_shift_swap", token: state.token, name: name, reason: reason })
			.then(function () {
				announce("Shift swap rejected.");
				show("shift_swaps");
			})
			.catch(function (err) {
				if (expired(err && err.message)) return;
				button.disabled = false;
				button.innerHTML = label;
				state.error = (err && err.message) || "Could not reject.";
				render();
			});
	}

	function shiftSwapsHtml(d) {
		var sent = d.sent || [];
		var received = d.received || [];
		var pendingReceived = d.pending_received || [];
		
		if (!sent.length && !received.length) {
			return emptyBlock("fa-exchange", "No shift swaps", "Request a swap with a colleague or respond to incoming requests.");
		}
		
		var html = '';
		
		// Pending received (action needed)
		if (pendingReceived.length) {
			html += '<p class="ep-card-title"><i class="fa fa-bell"></i> Awaiting Your Response (' + pendingReceived.length + ')</p>';
			pendingReceived.forEach(function (r) {
				html +=
					'<div class="ep-swap-card ep-swap-pending" style="border:1px solid var(--ep-warn);border-radius:.8rem;padding:1rem;margin-bottom:.75rem;background:color-mix(in srgb, var(--ep-warn) 5%, var(--ep-card))">' +
					'<div style="display:flex;gap:1rem;align-items:center;flex-wrap:wrap">' +
					'<div style="flex:1;min-width:200px">' +
					'<div class="ep-row-title">' + esc(r.requester_name) + ' wants to swap</div>' +
					'<div class="ep-row-meta">' + esc(dateOnly(r.shift_date)) + ' &middot; ' + esc(r.requester_shift) + ' \u2194 ' + esc(r.recipient_shift) + '</div>' +
					(r.reason ? '<div class="ep-row-meta" style="margin-top:.25rem"><i class="fa fa-comment-o"></i> ' + esc(r.reason) + '</div>' : '') +
					'</div>' +
					'<div style="display:flex;gap:.5rem">' +
					'<button type="button" class="ep-btn ep-btn-ghost swap-approve" data-name="' + esc(r.name) + '" style="min-width:100px"><i class="fa fa-check"></i> Approve</button>' +
					'<button type="button" class="ep-btn ep-btn-ghost swap-reject" data-name="' + esc(r.name) + '" style="min-width:100px;background:color-mix(in srgb, var(--ep-bad) 10%, transparent);color:var(--ep-bad);border-color:var(--ep-bad)"><i class="fa fa-times"></i> Reject</button>' +
					'</div>' +
					'</div>' +
					'</div>';
			});
		}
		
		// Sent requests
		if (sent.length) {
			html += '<p class="ep-card-title" style="margin-top:1.5rem"><i class="fa fa-paper-plane"></i> Your Requests (' + sent.length + ')</p>';
			sent.forEach(function (r) {
				var badgeClass = r.status === "Approved" ? "ep-badge--good" : r.status === "Rejected" ? "ep-badge--bad" : "ep-badge--info";
				html +=
					'<div class="ep-swap-card" style="border:1px solid var(--ep-line);border-radius:.8rem;padding:1rem;margin-bottom:.75rem;background:var(--ep-card)">' +
					'<div style="display:flex;gap:1rem;align-items:center;flex-wrap:wrap;justify-content:space-between">' +
					'<div style="flex:1;min-width:200px">' +
					'<div class="ep-row-title">Swap with ' + esc(r.recipient_name) + '</div>' +
					'<div class="ep-row-meta">' + esc(dateOnly(r.shift_date)) + ' &middot; ' + esc(r.requester_shift) + ' \u2194 ' + esc(r.recipient_shift) + '</div>' +
					(r.reason ? '<div class="ep-row-meta" style="margin-top:.25rem"><i class="fa fa-comment-o"></i> ' + esc(r.reason) + '</div>' : '') +
					'</div>' +
					'<span class="ep-badge ' + badgeClass + '" style="font-size:.85rem;padding:.35rem .7rem;flex-shrink:0">' + esc(r.status) + '</span>' +
					'</div>' +
					'</div>';
			});
		}
		
		// Other received (already responded)
		var otherReceived = received.filter(function (r) { return r.status !== "Pending"; });
		if (otherReceived.length) {
			html += '<p class="ep-card-title" style="margin-top:1.5rem"><i class="fa fa-inbox"></i> Other Requests (' + otherReceived.length + ')</p>';
			otherReceived.forEach(function (r) {
				var badgeClass = r.status === "Approved" ? "ep-badge--good" : "ep-badge--bad";
				html +=
					'<div class="ep-swap-card" style="border:1px solid var(--ep-line);border-radius:.8rem;padding:1rem;margin-bottom:.75rem;background:var(--ep-card)">' +
					'<div style="display:flex;gap:1rem;align-items:center;flex-wrap:wrap;justify-content:space-between">' +
					'<div style="flex:1;min-width:200px">' +
					'<div class="ep-row-title">Request from ' + esc(r.requester_name) + '</div>' +
					'<div class="ep-row-meta">' + esc(dateOnly(r.shift_date)) + ' &middot; ' + esc(r.requester_shift) + ' \u2194 ' + esc(r.recipient_shift) + '</div>' +
					(r.reason ? '<div class="ep-row-meta" style="margin-top:.25rem"><i class="fa fa-comment-o"></i> ' + esc(r.reason) + '</div>' : '') +
					'</div>' +
					'<span class="ep-badge ' + badgeClass + '" style="font-size:.85rem;padding:.35rem .7rem;flex-shrink:0">' + esc(r.status) + '</span>' +
					'</div>' +
					'</div>';
			});
		}
		
		return html;
	}

	function renderShiftSwapForm() {
		host.innerHTML =
			'<div class="ep-fade">' +
			'<button type="button" class="ep-back" id="formBack"><i class="fa fa-arrow-left"></i> Back</button>' +
			'<div class="ep-card" id="panelBody"><div class="ep-empty"><span class="ep-spin"></span></div></div>' +
			"</div>";
		document.getElementById("formBack").addEventListener("click", function () {
			show("shift_swaps");
		});
		
		// Fetch colleagues and shifts
		Promise.all([
			call({ action: "my_colleagues", token: state.token }),
			call({ action: "my_shifts", token: state.token }),
		]).then(function (results) {
			var colleagues = results[0].colleagues || [];
			var shifts = results[1].shifts || [];
			
			var colleagueOptions = colleagues
				.filter(function (c) { return c.name !== state.employee; })
				.map(function (c) {
					return '<option value="' + esc(c.name) + '" data-name="' + esc(c.employee_name) + '">' + esc(c.employee_name) + ' (' + esc(c.employee_number) + ')</option>';
				}).join('');
			
			var shiftOptions = shifts.map(function (s) {
				return '<option value="' + esc(s.name) + '">' + esc(s.shift_type) + ' (' + esc(s.start_time) + '-' + esc(s.end_time) + ')</option>';
			}).join('');
			
			var today = new Date().toISOString().split("T")[0];
			
			document.getElementById("panelBody").innerHTML =
				'<p class="ep-card-title">Request Shift Swap</p>' +
				'<form id="swapForm" novalidate>' +
				'<div class="ep-field"><label class="ep-label" for="swapColleague">Colleague</label>' +
				'<select class="ep-input" id="swapColleague" required>' +
				'<option value="">Select a colleague</option>' + colleagueOptions +
				'</select></div>' +
				'<div class="ep-field"><label class="ep-label" for="swapDate">Date</label>' +
				'<input class="ep-input" id="swapDate" type="date" value="' + today + '" min="' + today + '" required /></div>' +
				'<div style="display:grid;grid-template-columns:1fr 1fr;gap:.75rem">' +
				'<div class="ep-field"><label class="ep-label" for="swapMyShift">Your Shift</label>' +
				'<select class="ep-input" id="swapMyShift" required>' +
				'<option value="">Select your shift</option>' + shiftOptions +
				'</select></div>' +
				'<div class="ep-field"><label class="ep-label" for="swapTheirShift">Their Shift</label>' +
				'<select class="ep-input" id="swapTheirShift" required>' +
				'<option value="">Select their shift</option>' + shiftOptions +
				'</select></div>' +
				'</div>' +
				'<div class="ep-field"><label class="ep-label" for="swapReason">Reason (optional)</label>' +
				'<textarea class="ep-input" id="swapReason" rows="3" placeholder="Why do you need to swap?"></textarea></div>' +
				'<button class="ep-btn" type="submit">Send Request</button></form>';
			
			document.getElementById("swapForm").addEventListener("submit", function (e) {
				e.preventDefault();
				submitShiftSwapForm();
			});
		}).catch(function (err) {
			if (expired(err && err.message)) return;
			document.getElementById("panelBody").innerHTML = emptyBlock(
				"fa-exclamation-triangle",
				"Cannot load form",
				(err && err.message) || ""
			);
		});
	}

	function submitShiftSwapForm() {
		call({
			action: "request_shift_swap",
			token: state.token,
			recipient: document.getElementById("swapColleague").value,
			shift_date: document.getElementById("swapDate").value,
			requester_shift: document.getElementById("swapMyShift").value,
			recipient_shift: document.getElementById("swapTheirShift").value,
			reason: document.getElementById("swapReason").value,
		})
			.then(function () {
				announce("Shift swap request sent.");
				show("shift_swaps");
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

	function renderBenefits() {
		host.innerHTML =
			'<div class="ep-fade">' +
			'<button type="button" class="ep-back" id="panelBack"><i class="fa fa-arrow-left"></i> Back</button>' +
			alertHtml() +
			'<div class="ep-card" id="panelBody"><div class="ep-empty"><span class="ep-spin"></span></div></div>' +
			"</div>";
		document.getElementById("panelBack").addEventListener("click", function () {
			show("dashboard");
		});
		call({ action: "my_benefits", token: state.token })
			.then(function (d) {
				document.getElementById("panelBody").innerHTML = benefitsHtml(d);
			})
			.catch(function (err) {
				if (expired(err && err.message)) return;
				document.getElementById("panelBody").innerHTML = emptyBlock(
					"fa-exclamation-triangle",
					"Cannot load benefits",
					(err && err.message) || ""
				);
			});
	}

	function benefitsHtml(d) {
		var enrollments = d.enrollments || [];
		var claims = d.claims || [];
		var wellness = d.wellness || [];
		
		if (!enrollments.length && !claims.length && !wellness.length) {
			return emptyBlock("fa-heartbeat", "No benefits enrolled", "Your insurance and wellness programs will appear here once enrolled.");
		}
		
		var html = '';
		
		// Summary stats
		if (claims.length) {
			html +=
				'<div class="ep-benefits-summary" style="display:flex;gap:1rem;flex-wrap:wrap;margin-bottom:1.5rem">' +
				'<span class="ep-badge ep-badge--info" style="font-size:.9rem;padding:.5rem .8rem"><i class="fa fa-file-text-o"></i> ' + esc(d.total_claims || 0) + ' Total Claims</span>' +
				'<span class="ep-badge ep-badge--warn" style="font-size:.9rem;padding:.5rem .8rem"><i class="fa fa-clock-o"></i> ' + esc(d.pending_claims || 0) + ' Pending</span>' +
				'<span class="ep-badge ep-badge--good" style="font-size:.9rem;padding:.5rem .8rem"><i class="fa fa-check-circle"></i> ' + esc(d.approved_claims || 0) + ' Approved</span>' +
				'</div>';
		}
		
		// Insurance Enrollments
		if (enrollments.length) {
			html += '<p class="ep-card-title"><i class="fa fa-shield"></i> Insurance Coverage</p>';
			enrollments.forEach(function (e) {
				var statusClass = e.status === "Active" ? "ep-badge--good" : e.status === "Expired" ? "ep-badge--bad" : "ep-badge--warn";
				var dependents = e.dependents || [];
				html +=
					'<div class="ep-benefit-card" style="border:1px solid var(--ep-line);border-radius:.8rem;padding:1.25rem;margin-bottom:1rem;background:var(--ep-card)">' +
					'<div style="display:flex;gap:1rem;align-items:flex-start;flex-wrap:wrap;justify-content:space-between">' +
					'<div style="flex:1;min-width:250px">' +
					'<div style="display:flex;align-items:center;gap:.5rem;margin-bottom:.5rem">' +
					'<strong style="font-size:1.1rem">' + esc(e.plan_name || e.insurance_scheme) + '</strong>' +
					'<span class="ep-badge ' + statusClass + '">' + esc(e.status) + '</span>' +
					'</div>' +
					'<div class="ep-row-meta"><i class="fa fa-building"></i> ' + esc(e.insurance_company) + '</div>' +
					'<div class="ep-row-meta"><i class="fa fa-tag"></i> ' + esc(e.coverage_type) + ' &middot; Sum Insured: ' + esc(e.sum_insured) + '</div>' +
					'<div class="ep-row-meta"><i class="fa fa-credit-card"></i> Policy: ' + esc(e.policy_number || "—") + (e.card_number ? ' &middot; Card: ' + esc(e.card_number) : '') + '</div>' +
					'<div class="ep-row-meta"><i class="fa fa-calendar"></i> ' + esc(dateOnly(e.start_date)) + (e.end_date ? ' &rarr; ' + esc(dateOnly(e.end_date)) : '') + '</div>' +
					(dependents.length ? '<div style="margin-top:.5rem"><strong>Covered Dependents:</strong> ' + dependents.map(function(dep) { return esc(dep.dependent_name) + ' (' + esc(dep.relation) + ')'; }).join(', ') + '</div>' : '') +
					'</div>' +
					(e.network_providers ? '<div style="margin-top:.75rem;padding:.75rem;background:color-mix(in srgb, var(--ep-muted) 5%, var(--ep-card));border-radius:.5rem;font-size:.85rem"><strong>Network Providers:</strong> ' + esc(e.network_providers) + '</div>' : '') +
					'</div>' +
					'</div>';
			});
		}
		
		// Recent Claims
		if (claims.length) {
			html += '<p class="ep-card-title" style="margin-top:1.5rem"><i class="fa fa-file-text-o"></i> Recent Claims</p>';
			claims.forEach(function (c) {
				var statusClass = c.status === "Approved" ? "ep-badge--good" : c.status === "Rejected" ? "ep-badge--bad" : c.status === "Paid" ? "ep-badge--info" : "ep-badge--warn";
				html +=
					'<div class="ep-claim-row" style="border:1px solid var(--ep-line);border-radius:.8rem;padding:1rem;margin-bottom:.75rem;background:var(--ep-card)">' +
					'<div style="display:flex;gap:1rem;align-items:center;flex-wrap:wrap;justify-content:space-between">' +
					'<div style="flex:1;min-width:200px">' +
					'<div class="ep-row-title">' + esc(c.claim_type) + ' &middot; ' + esc(c.hospital_provider) + '</div>' +
					'<div class="ep-row-meta">' + esc(dateOnly(c.claim_date)) + ' &middot; Claimed: ' + esc(c.amount_claimed) + (c.amount_approved ? ' &middot; Approved: ' + esc(c.amount_approved) : '') + '</div>' +
					(c.description ? '<div class="ep-row-meta" style="margin-top:.25rem">' + esc(c.description) + '</div>' : '') +
					'</div>' +
					'<span class="ep-badge ' + statusClass + '" style="font-size:.85rem;padding:.35rem .7rem;flex-shrink:0">' + esc(c.status) + '</span>' +
					'</div>' +
					'</div>';
			});
		}
		
		// Wellness Programs
		if (wellness.length) {
			html += '<p class="ep-card-title" style="margin-top:1.5rem"><i class="fa fa-leaf"></i> Wellness Programs</p>';
			wellness.forEach(function (w) {
				var statusClass = w.status === "Completed" ? "ep-badge--good" : w.status === "In Progress" ? "ep-badge--info" : "ep-badge--warn";
				var progress = w.progress || 0;
				html +=
					'<div class="ep-wellness-card" style="border:1px solid var(--ep-line);border-radius:.8rem;padding:1.25rem;margin-bottom:1rem;background:var(--ep-card)">' +
					'<div style="display:flex;gap:1rem;align-items:center;flex-wrap:wrap;justify-content:space-between">' +
					'<div style="flex:1;min-width:250px">' +
					'<div class="ep-row-title">' + esc(w.program_name) + '</div>' +
					'<div class="ep-row-meta">' + esc(dateOnly(w.start_date)) + (w.end_date ? ' &rarr; ' + esc(dateOnly(w.end_date)) : '') + '</div>' +
					(w.description ? '<div class="ep-row-meta" style="margin-top:.25rem">' + esc(w.description) + '</div>' : '') +
					'</div>' +
					'<div style="text-align:right;min-width:150px">' +
					'<div class="ep-badge ' + statusClass + '" style="margin-bottom:.5rem">' + esc(w.status) + '</div>' +
					'<div style="width:120px;height:8px;background:color-mix(in srgb, var(--ep-muted) 15%, transparent);border-radius:4px;overflow:hidden">' +
					'<div style="width:' + progress + '%;height:100%;background:var(--ep-accent);transition:width .3s"></div>' +
					'</div>' +
					'<div style="font-size:.8rem;color:var(--ep-muted);margin-top:.25rem">' + progress + '% complete</div>' +
					'</div>' +
					'</div>' +
					'</div>';
			});
		}
		
		return html;
	}

	function renderLearning() {
		host.innerHTML =
			'<div class="ep-fade">' +
			'<button type="button" class="ep-back" id="panelBack"><i class="fa fa-arrow-left"></i> Back</button>' +
			alertHtml() +
			'<div class="ep-card" id="panelBody"><div class="ep-empty"><span class="ep-spin"></span></div></div>' +
			"</div>";
		document.getElementById("panelBack").addEventListener("click", function () {
			show("dashboard");
		});
		call({ action: "my_learning", token: state.token })
			.then(function (d) {
				document.getElementById("panelBody").innerHTML = learningHtml(d);
			})
			.catch(function (err) {
				if (expired(err && err.message)) return;
				document.getElementById("panelBody").innerHTML = emptyBlock(
					"fa-exclamation-triangle",
					"Cannot load learning",
					(err && err.message) || ""
				);
			});
	}

	function learningHtml(d) {
		var enrollments = d.enrollments || [];
		var certifications = d.certifications || [];
		var learningPaths = d.learning_paths || [];
		var completed = d.completed_courses || [];
		
		if (!enrollments.length && !certifications.length && !learningPaths.length) {
			return emptyBlock("fa-graduation-cap", "No learning records", "Your courses, certifications, and learning paths will appear here once enrolled.");
		}
		
		var html = '';
		
		// Summary stats
		if (enrollments.length || certifications.length || learningPaths.length) {
			html +=
				'<div class="ep-learning-summary" style="display:flex;gap:1rem;flex-wrap:wrap;margin-bottom:1.5rem">' +
				'<span class="ep-badge ep-badge--info" style="font-size:.9rem;padding:.5rem .8rem"><i class="fa fa-clock-o"></i> ' + esc(d.in_progress || 0) + ' In Progress</span>' +
				'<span class="ep-badge ep-badge--warn" style="font-size:.9rem;padding:.5rem .8rem"><i class="fa fa-play-circle"></i> ' + esc(d.not_started || 0) + ' Not Started</span>' +
				'<span class="ep-badge ep-badge--good" style="font-size:.9rem;padding:.5rem .8rem"><i class="fa fa-check-circle"></i> ' + esc(d.completed_count || 0) + ' Completed</span>' +
				'<span class="ep-badge ep-badge--bad" style="font-size:.9rem;padding:.5rem .8rem"><i class="fa fa-exclamation-triangle"></i> ' + esc(d.overdue || 0) + ' Overdue</span>' +
				'<span class="ep-badge" style="font-size:.9rem;padding:.5rem .8rem;background:color-mix(in srgb, var(--ep-muted) 15%, transparent);color:var(--ep-muted)"><i class="fa fa-certificate"></i> ' + esc(d.expiring_certs || 0) + ' Expiring Soon</span>' +
				'</div>';
		}
		
		// Course Enrollments
		if (enrollments.length) {
			html += '<p class="ep-card-title"><i class="fa fa-book"></i> Course Enrollments</p>';
			enrollments.forEach(function (e) {
				var statusClass = e.status === "Completed" ? "ep-badge--good" : e.status === "In Progress" ? "ep-badge--info" : e.status === "Overdue" || (e.due_date && e.due_date < new Date().toISOString().split("T")[0] && e.status !== "Completed") ? "ep-badge--bad" : "ep-badge--warn";
				var isOverdue = e.due_date && e.due_date < new Date().toISOString().split("T")[0] && e.status !== "Completed";
				var progress = e.progress || 0;
				html +=
					'<div class="ep-learning-card" style="border:1px solid var(--ep-line);border-radius:.8rem;padding:1.25rem;margin-bottom:1rem;background:var(--ep-card)">' +
					'<div style="display:flex;gap:1rem;align-items:flex-start;flex-wrap:wrap;justify-content:space-between">' +
					'<div style="flex:1;min-width:250px">' +
					'<div style="display:flex;align-items:center;gap:.5rem;margin-bottom:.5rem">' +
					'<strong style="font-size:1.1rem">' + esc(e.course_name || e.course) + '</strong>' +
					'<span class="ep-badge ' + statusClass + '">' + esc(e.status) + (isOverdue ? ' (Overdue)' : '') + '</span>' +
					'</div>' +
					'<div class="ep-row-meta"><i class="fa fa-calendar"></i> ' + esc(dateOnly(e.start_date)) + (e.end_date ? ' &rarr; ' + esc(dateOnly(e.end_date)) : '') + (e.due_date ? ' &middot; Due: ' + esc(dateOnly(e.due_date)) : '') + '</div>' +
					'<div class="ep-row-meta"><i class="fa fa-clock-o"></i> ' + esc(e.hours || 0) + ' hrs' + (e.training_provider ? ' &middot; ' + esc(e.training_provider) : '') + '</div>' +
					(e.is_mandatory ? '<div class="ep-row-meta" style="color:var(--ep-warn)"><i class="fa fa-exclamation-circle"></i> Mandatory</div>' : '') +
					'</div>' +
					'<div style="text-align:right;min-width:150px">' +
					'<div style="width:120px;height:8px;background:color-mix(in srgb, var(--ep-muted) 15%, transparent);border-radius:4px;overflow:hidden;margin-bottom:.5rem">' +
					'<div style="width:' + progress + '%;height:100%;background:' + (progress === 100 ? 'var(--ep-good)' : 'var(--ep-accent)') + ';transition:width .3s"></div>' +
					'</div>' +
					'<div style="font-size:.8rem;color:var(--ep-muted)">' + progress + '% complete</div>' +
					(e.certificate_number ? '<a href="#" style="font-size:.8rem;color:var(--ep-accent);margin-top:.5rem;display:block"><i class="fa fa-certificate"></i> Certificate: ' + esc(e.certificate_number) + '</a>' : '') +
					(e.score ? '<div style="font-size:.8rem;color:var(--ep-muted);margin-top:.25rem">Score: ' + esc(e.score) + '%</div>' : '') +
					'</div>' +
					'</div>' +
					'</div>';
			});
		}
		
		// Certifications
		if (certifications.length) {
			html += '<p class="ep-card-title" style="margin-top:1.5rem"><i class="fa fa-certificate"></i> Certifications</p>';
			certifications.forEach(function (c) {
				var statusClass = c.status === "Active" ? "ep-badge--good" : c.status === "Expired" ? "ep-badge--bad" : "ep-badge--warn";
				var isExpiring = c.expiry_date && c.expiry_date < new Date(Date.now() + 90*24*60*60*1000).toISOString().split("T")[0] && c.status === "Active";
				html +=
					'<div class="ep-cert-card" style="border:1px solid var(--ep-line);border-radius:.8rem;padding:1.25rem;margin-bottom:1rem;background:var(--ep-card)"' + (isExpiring ? ' style="border-left:4px solid var(--ep-warn)"' : '') + '>' +
					'<div style="display:flex;gap:1rem;align-items:center;flex-wrap:wrap;justify-content:space-between">' +
					'<div style="flex:1;min-width:250px">' +
					'<div style="display:flex;align-items:center;gap:.5rem;margin-bottom:.5rem">' +
					'<strong style="font-size:1.1rem">' + esc(c.certification_name) + '</strong>' +
					'<span class="ep-badge ' + statusClass + '">' + esc(c.status) + '</span>' +
					(isExpiring ? '<span class="ep-badge ep-badge--warn" style="font-size:.7rem"><i class="fa fa-clock-o"></i> Expiring Soon</span>' : '') +
					'</div>' +
					'<div class="ep-row-meta"><i class="fa fa-building"></i> ' + esc(c.issuing_body) + '</div>' +
					'<div class="ep-row-meta"><i class="fa fa-calendar"></i> Issued: ' + esc(dateOnly(c.issue_date)) + (c.expiry_date ? ' &middot; Expires: ' + esc(dateOnly(c.expiry_date)) : '') + '</div>' +
					(c.credential_id ? '<div class="ep-row-meta"><i class="fa fa-id-badge"></i> Credential: ' + esc(c.credential_id) + '</div>' : '') +
					'</div>' +
					(c.credential_url ? '<a href="' + esc(c.credential_url) + '" target="_blank" style="font-size:.85rem;color:var(--ep-accent)"><i class="fa fa-external-link"></i> Verify</a>' : '') +
					'</div>';
			});
		}
		
		// Learning Paths
		if (learningPaths.length) {
			html += '<p class="ep-card-title" style="margin-top:1.5rem"><i class="fa fa-road"></i> Learning Paths</p>';
			learningPaths.forEach(function (lp) {
				var statusClass = lp.status === "Completed" ? "ep-badge--good" : lp.status === "In Progress" ? "ep-badge--info" : "ep-badge--warn";
				var progress = lp.progress || 0;
				html +=
					'<div class="ep-lpath-card" style="border:1px solid var(--ep-line);border-radius:.8rem;padding:1.25rem;margin-bottom:1rem;background:var(--ep-card)">' +
					'<div style="display:flex;gap:1rem;align-items:center;flex-wrap:wrap;justify-content:space-between">' +
					'<div style="flex:1;min-width:250px">' +
					'<div class="ep-row-title">' + esc(lp.learning_path_name || lp.learning_path) + '</div>' +
					'<div class="ep-row-meta">' + esc(dateOnly(lp.start_date)) + (lp.target_end_date ? ' &rarr; ' + esc(dateOnly(lp.target_end_date)) : '') + '</div>' +
					'</div>' +
					'<div style="text-align:right;min-width:150px">' +
					'<div class="ep-badge ' + statusClass + '" style="margin-bottom:.5rem">' + esc(lp.status) + '</div>' +
					'<div style="width:120px;height:8px;background:color-mix(in srgb, var(--ep-muted) 15%, transparent);border-radius:4px;overflow:hidden">' +
					'<div style="width:' + progress + '%;height:100%;background:var(--ep-accent);transition:width .3s"></div>' +
					'</div>' +
					'<div style="font-size:.8rem;color:var(--ep-muted);margin-top:.25rem">' + progress + '% complete</div>' +
					'</div>' +
					'</div>' +
					'</div>';
			});
		}
		
		return html;
	}

	function renderAssets() {
		host.innerHTML =
			'<div class="ep-fade">' +
			'<button type="button" class="ep-back" id="panelBack"><i class="fa fa-arrow-left"></i> Back</button>' +
			'<button type="button" class="ep-btn ep-btn-ghost" id="newAssetReq" style="margin-bottom:.8rem;float:right"><i class="fa fa-plus"></i> Request Equipment</button>' +
			alertHtml() +
			'<div class="ep-card" id="panelBody"><div class="ep-empty"><span class="ep-spin"></span></div></div>' +
			"</div>";
		document.getElementById("panelBack").addEventListener("click", function () {
			show("dashboard");
		});
		document.getElementById("newAssetReq").addEventListener("click", function () {
			show("asset_form");
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
		var requests = d.requests || [];
		
		var html = '';
		
		// Summary stats
		if (assets.length || requests.length) {
			html +=
				'<div class="ep-assets-summary" style="display:flex;gap:1rem;flex-wrap:wrap;margin-bottom:1.5rem">' +
				'<span class="ep-badge ep-badge--info" style="font-size:.9rem;padding:.5rem .8rem"><i class="fa fa-laptop"></i> ' + esc(d.total_assets || 0) + ' Total</span>' +
				'<span class="ep-badge ep-badge--good" style="font-size:.9rem;padding:.5rem .8rem"><i class="fa fa-check-circle"></i> ' + esc(d.active_assets || 0) + ' Active</span>' +
				'<span class="ep-badge ep-badge--warn" style="font-size:.9rem;padding:.5rem .8rem"><i class="fa fa-shield"></i> ' + esc(d.maintenance_due || 0) + ' Warranty Expiring</span>' +
				'<span class="ep-badge" style="font-size:.9rem;padding:.5rem .8rem;background:color-mix(in srgb, var(--ep-muted) 15%, transparent);color:var(--ep-muted)"><i class="fa fa-clock-o"></i> ' + esc(requests.length) + ' Requests</span>' +
				'</div>';
		}
		
		// Assigned Assets
		if (assets.length) {
			html += '<p class="ep-card-title"><i class="fa fa-laptop"></i> Assigned Equipment</p>';
			assets.forEach(function (a) {
				var statusClass = a.status === "In Use" ? "ep-badge--good" : a.status === "Under Maintenance" ? "ep-badge--warn" : "ep-badge";
				var maint = a.maintenance || [];
				var nextMaint = maint.length ? maint[0] : null;
				html +=
					'<div class="ep-asset-card" style="border:1px solid var(--ep-line);border-radius:.8rem;padding:1.25rem;margin-bottom:1rem;background:var(--ep-card)">' +
					'<div style="display:flex;gap:1rem;align-items:flex-start;flex-wrap:wrap;justify-content:space-between">' +
					'<div style="flex:1;min-width:250px">' +
					'<div style="display:flex;align-items:center;gap:.5rem;margin-bottom:.5rem">' +
					'<strong style="font-size:1.1rem">' + esc(a.asset_name || a.item_code) + '</strong>' +
					'<span class="ep-badge ' + statusClass + '">' + esc(a.status) + '</span>' +
					'</div>' +
					'<div class="ep-row-meta"><i class="fa fa-tag"></i> ' + esc(a.asset_category) + (a.serial_no ? ' &middot; SN: ' + esc(a.serial_no) : '') + '</div>' +
					'<div class="ep-row-meta"><i class="fa fa-calendar"></i> Assigned: ' + esc(dateOnly(a.assigned_date)) + (a.expected_return_date ? ' &middot; Return by: ' + esc(dateOnly(a.expected_return_date)) : '') + '</div>' +
					'<div class="ep-row-meta"><i class="fa fa-dollar"></i> Value: ' + esc(a.purchase_amount) + (a.purchase_date ? ' &middot; Purchased: ' + esc(dateOnly(a.purchase_date)) : '') + '</div>' +
					(a.warranty_expiry ? '<div class="ep-row-meta" style="color:' + (a.warranty_expiry < new Date(Date.now() + 30*24*60*60*1000).toISOString().split("T")[0] ? 'var(--ep-warn)' : 'var(--ep-muted)') + '"><i class="fa fa-shield"></i> Warranty: ' + esc(dateOnly(a.warranty_expiry)) + (a.warranty_expiry < new Date(Date.now() + 30*24*60*60*1000).toISOString().split("T")[0] ? ' (Expiring Soon)' : '') + '</div>' : '') +
					(a.condition ? '<div class="ep-row-meta"><i class="fa fa-heartbeat"></i> Condition: ' + esc(a.condition) + '</div>' : '') +
					(a.location ? '<div class="ep-row-meta"><i class="fa fa-map-marker"></i> Location: ' + esc(a.location) + '</div>' : '') +
					'</div>' +
					(maint.length ? '<div style="margin-top:.75rem;padding:.75rem;background:color-mix(in srgb, var(--ep-muted) 5%, var(--ep-card));border-radius:.5rem;font-size:.85rem"><strong>Maintenance:</strong> ' + maint.map(function(m) { return esc(m.maintenance_type) + ' - ' + esc(dateOnly(m.scheduled_date)) + ' (' + esc(m.status) + ')'; }).join(', ') + '</div>' : '') +
					'</div>' +
					'</div>';
			});
		} else {
			html += emptyBlock("fa-laptop", "No equipment assigned", "Your assigned assets will appear here.");
		}
		
		// Asset Requests
		if (requests.length) {
			html += '<p class="ep-card-title" style="margin-top:1.5rem"><i class="fa fa-plus-circle"></i> Your Requests</p>';
			requests.forEach(function (r) {
				var statusClass = r.status === "Approved" ? "ep-badge--good" : r.status === "Rejected" ? "ep-badge--bad" : r.status === "Fulfilled" ? "ep-badge--info" : "ep-badge--warn";
				html +=
					'<div class="ep-asset-req" style="border:1px solid var(--ep-line);border-radius:.8rem;padding:1rem;margin-bottom:.75rem;background:var(--ep-card)">' +
					'<div style="display:flex;gap:1rem;align-items:center;flex-wrap:wrap;justify-content:space-between">' +
					'<div style="flex:1;min-width:200px">' +
					'<div class="ep-row-title">' + esc(r.item_name) + ' <span class="ep-badge" style="font-size:.7rem;background:color-mix(in srgb, var(--ep-muted) 15%, transparent);color:var(--ep-muted)">' + esc(r.asset_category) + '</span></div>' +
					'<div class="ep-row-meta">' + esc(r.purpose) + '</div>' +
					'<div class="ep-row-meta">Priority: ' + esc(r.priority) + ' &middot; Requested: ' + esc(dateOnly(r.requested_date)) + (r.approved_date ? ' &middot; Approved: ' + esc(dateOnly(r.approved_date)) : '') + '</div>' +
					'</div>' +
					'<span class="ep-badge ' + statusClass + '" style="font-size:.85rem;padding:.35rem .7rem;flex-shrink:0">' + esc(r.status) + '</span>' +
					'</div>' +
					'</div>';
			});
		}
		
		return html;
	}

	function renderAssetForm() {
		host.innerHTML =
			'<div class="ep-fade">' +
			'<button type="button" class="ep-back" id="formBack"><i class="fa fa-arrow-left"></i> Back</button>' +
			'<div class="ep-card" id="panelBody"><div class="ep-empty"><span class="ep-spin"></span></div></div>' +
			"</div>";
		document.getElementById("formBack").addEventListener("click", function () {
			show("assets");
		});
		call({ action: "asset_categories", token: state.token })
			.then(function (d) {
				var cats = (d.asset_categories || []).map(function (c) {
					return '<option value="' + esc(c.name) + '">' + esc(c.category_name || c.name) + '</option>';
				}).join('');
				document.getElementById("panelBody").innerHTML =
					'<p class="ep-card-title">Request Equipment</p>' +
					'<form id="assetForm" novalidate>' +
					'<div class="ep-field"><label class="ep-label" for="reqCategory">Category</label>' +
					'<select class="ep-input" id="reqCategory" required>' +
					'<option value="">Select category</option>' + cats +
					'</select></div>' +
					'<div class="ep-field"><label class="ep-label" for="reqItem">Item Name / Description</label>' +
					'<input class="ep-input" id="reqItem" placeholder="e.g., MacBook Pro 16\", Dell Monitor 27\"" required /></div>' +
					'<div class="ep-field"><label class="ep-label" for="reqPurpose">Purpose / Justification</label>' +
					'<textarea class="ep-input" id="reqPurpose" rows="3" placeholder="Why do you need this equipment?" required></textarea></div>' +
					'<div class="ep-field"><label class="ep-label" for="reqPriority">Priority</label>' +
					'<select class="ep-input" id="reqPriority">' +
					'<option value="Low">Low</option>' +
					'<option value="Medium" selected>Medium</option>' +
					'<option value="High">High</option>' +
					'<option value="Urgent">Urgent</option>' +
					'</select></div>' +
					'<div class="ep-field"><label class="ep-label" for="reqExpected">Expected Date (optional)</label>' +
					'<input class="ep-input" id="reqExpected" type="date" /></div>' +
					'<button class="ep-btn" type="submit">Submit Request</button></form>';
				document.getElementById("assetForm").addEventListener("submit", function (e) {
					e.preventDefault();
					submitAssetForm();
				});
			})
			.catch(function (err) {
				if (expired(err && err.message)) return;
				document.getElementById("panelBody").innerHTML = emptyBlock(
					"fa-exclamation-triangle",
					"Cannot load categories",
					(err && err.message) || ""
				);
			});
	}

	function submitAssetForm() {
		call({
			action: "request_asset",
			token: state.token,
			asset_category: document.getElementById("reqCategory").value,
			item_name: document.getElementById("reqItem").value,
			purpose: document.getElementById("reqPurpose").value,
			priority: document.getElementById("reqPriority").value,
			expected_date: document.getElementById("reqExpected").value || null,
		})
			.then(function () {
				announce("Asset request submitted.");
				show("assets");
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

	function renderTravel() {
		host.innerHTML =
			'<div class="ep-fade">' +
			'<button type="button" class="ep-back" id="panelBack"><i class="fa fa-arrow-left"></i> Back</button>' +
			'<button type="button" class="ep-btn ep-btn-ghost" id="newTravel" style="margin-bottom:.8rem;float:right"><i class="fa fa-plus"></i> Request Travel</button>' +
			alertHtml() +
			'<div class="ep-card" id="panelBody"><div class="ep-empty"><span class="ep-spin"></span></div></div>' +
			"</div>";
		document.getElementById("panelBack").addEventListener("click", function () {
			show("dashboard");
		});
		document.getElementById("newTravel").addEventListener("click", function () {
			show("travel_form");
		});
		call({ action: "my_travel", token: state.token })
			.then(function (d) {
				document.getElementById("panelBody").innerHTML = travelHtml(d);
			})
			.catch(function (err) {
				if (expired(err && err.message)) return;
				document.getElementById("panelBody").innerHTML = emptyBlock(
					"fa-exclamation-triangle",
					"Cannot load travel",
					(err && err.message) || ""
				);
			});
	}

	function travelHtml(d) {
		var rows = d.travel || [];
		if (!rows.length) {
			return emptyBlock("fa-plane", "No travel requests", "Your travel requests will appear here.");
		}
		var html =
			'<p class="ep-card-title">Travel Requests</p>' +
			'<div class="ep-travel-summary" style="display:flex;gap:1rem;flex-wrap:wrap;margin-bottom:1rem">' +
			'<span class="ep-badge ep-badge--warn" style="font-size:.9rem;padding:.5rem .8rem"><i class="fa fa-clock-o"></i> ' + esc(d.pending || 0) + ' Pending</span>' +
			'<span class="ep-badge ep-badge--good" style="font-size:.9rem;padding:.5rem .8rem"><i class="fa fa-check-circle"></i> ' + esc(d.approved || 0) + ' Approved</span>' +
			'<span class="ep-badge ep-badge--info" style="font-size:.9rem;padding:.5rem .8rem"><i class="fa fa-flag-checkered"></i> ' + esc(d.completed || 0) + ' Completed</span>' +
			'</div>';
		rows.forEach(function (r) {
			var statusClass = r.approval_status === "Approved" ? "ep-badge--good" : r.approval_status === "Rejected" ? "ep-badge--bad" : r.status === "Completed" ? "ep-badge--info" : "ep-badge--warn";
			html +=
				'<div class="ep-travel-row" style="border:1px solid var(--ep-line);border-radius:.8rem;padding:1rem;margin-bottom:.75rem;background:var(--ep-card)">' +
				'<div style="display:flex;gap:1rem;align-items:center;flex-wrap:wrap;justify-content:space-between">' +
				'<div style="flex:1;min-width:250px">' +
				'<div style="display:flex;align-items:center;gap:.5rem;margin-bottom:.5rem">' +
				'<strong style="font-size:1.1rem">' + esc(r.purpose) + '</strong>' +
				'<span class="ep-badge ' + statusClass + '">' + esc(r.approval_status || r.status) + '</span>' +
				'</div>' +
				'<div class="ep-row-meta"><i class="fa fa-map-marker"></i> ' + esc(r.destination) + ' &middot; ' + esc(r.mode_of_travel) + '</div>' +
				'<div class="ep-row-meta"><i class="fa fa-calendar"></i> ' + esc(dateOnly(r.departure_date)) + ' &rarr; ' + esc(dateOnly(r.return_date)) + '</div>' +
				'<div class="ep-row-meta"><i class="fa fa-money"></i> Estimated: ' + esc(r.estimated_cost) + (r.advance_amount ? ' &middot; Advance: ' + esc(r.advance_amount) : '') + (r.actual_cost ? ' &middot; Actual: ' + esc(r.actual_cost) : '') + '</div>' +
				(r.description ? '<div class="ep-row-meta" style="margin-top:.25rem">' + esc(r.description) + '</div>' : '') +
				'</div>' +
				'</div>' +
				'</div>';
		});
		return html;
	}

	function renderTravelForm() {
		host.innerHTML =
			'<div class="ep-fade">' +
			'<button type="button" class="ep-back" id="formBack"><i class="fa fa-arrow-left"></i> Back</button>' +
			'<div class="ep-card" id="panelBody"><div class="ep-empty"><span class="ep-spin"></span></div></div>' +
			"</div>";
		document.getElementById("formBack").addEventListener("click", function () {
			show("travel");
		});
		call({ action: "travel_modes", token: state.token })
			.then(function (d) {
				var modes = (d.travel_modes || []).map(function (m) {
					return '<option value="' + esc(m.name) + '">' + esc(m.mode_name || m.name) + '</option>';
				}).join('');
				var today = new Date().toISOString().split("T")[0];
				var nextWeek = new Date(Date.now() + 7*24*60*60*1000).toISOString().split("T")[0];
				document.getElementById("panelBody").innerHTML =
					'<p class="ep-card-title">Request Travel</p>' +
					'<form id="travelForm" novalidate>' +
					'<div class="ep-field"><label class="ep-label" for="trPurpose">Purpose</label>' +
					'<textarea class="ep-input" id="trPurpose" rows="3" placeholder="Business meeting, conference, training..." required></textarea></div>' +
					'<div style="display:grid;grid-template-columns:1fr 1fr;gap:.75rem">' +
					'<div class="ep-field"><label class="ep-label" for="trDeparture">Departure Date</label>' +
					'<input class="ep-input" id="trDeparture" type="date" value="' + today + '" min="' + today + '" required /></div>' +
					'<div class="ep-field"><label class="ep-label" for="trReturn">Return Date</label>' +
					'<input class="ep-input" id="trReturn" type="date" value="' + nextWeek + '" min="' + today + '" required /></div>' +
					'</div>' +
					'<div class="ep-field"><label class="ep-label" for="trDestination">Destination</label>' +
					'<input class="ep-input" id="trDestination" placeholder="City, Country" required /></div>' +
					'<div class="ep-field"><label class="ep-label" for="trMode">Mode of Travel</label>' +
					'<select class="ep-input" id="trMode" required>' +
					'<option value="">Select mode</option>' + modes +
					'</select></div>' +
					'<div style="display:grid;grid-template-columns:1fr 1fr;gap:.75rem">' +
					'<div class="ep-field"><label class="ep-label" for="trEstCost">Estimated Cost</label>' +
					'<input class="ep-input" id="trEstCost" type="number" step="0.01" min="0" required /></div>' +
					'<div class="ep-field"><label class="ep-label" for="trAdvance">Advance Amount (optional)</label>' +
					'<input class="ep-input" id="trAdvance" type="number" step="0.01" min="0" value="0" /></div>' +
					'</div>' +
					'<div class="ep-field"><label class="ep-label" for="trDesc">Description (optional)</label>' +
					'<textarea class="ep-input" id="trDesc" rows="3" placeholder="Additional details..."></textarea></div>' +
					'<button class="ep-btn" type="submit">Submit Request</button></form>';
				document.getElementById("travelForm").addEventListener("submit", function (e) {
					e.preventDefault();
					submitTravelForm();
				});
			})
			.catch(function (err) {
				if (expired(err && err.message)) return;
				document.getElementById("panelBody").innerHTML = emptyBlock(
					"fa-exclamation-triangle",
					"Cannot load travel modes",
					(err && err.message) || ""
				);
			});
	}

	function submitTravelForm() {
		var dep = document.getElementById("trDeparture").value;
		var ret = document.getElementById("trReturn").value;
		if (dep > ret) {
			var body = document.getElementById("panelBody");
			if (body) {
				body.innerHTML =
					'<div class="ep-alert ep-alert--bad"><i class="fa fa-exclamation-triangle"></i><span>Departure date cannot be after return date.</span></div>' + body.innerHTML;
			}
			return;
		}
		call({
			action: "request_travel",
			token: state.token,
			purpose: document.getElementById("trPurpose").value,
			departure_date: dep,
			return_date: ret,
			destination: document.getElementById("trDestination").value,
			mode_of_travel: document.getElementById("trMode").value,
			estimated_cost: parseFloat(document.getElementById("trEstCost").value),
			advance_amount: parseFloat(document.getElementById("trAdvance").value) || 0,
			description: document.getElementById("trDesc").value,
		})
			.then(function () {
				announce("Travel request submitted.");
				show("travel");
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

	function renderLoans() {
		host.innerHTML =
			'<div class="ep-fade">' +
			'<button type="button" class="ep-back" id="panelBack"><i class="fa fa-arrow-left"></i> Back</button>' +
			'<button type="button" class="ep-btn ep-btn-ghost" id="newLoan" style="margin-bottom:.8rem;float:right"><i class="fa fa-plus"></i> Request Loan</button>' +
			alertHtml() +
			'<div class="ep-card" id="panelBody"><div class="ep-empty"><span class="ep-spin"></span></div></div>' +
			"</div>";
		document.getElementById("panelBack").addEventListener("click", function () {
			show("dashboard");
		});
		document.getElementById("newLoan").addEventListener("click", function () {
			show("loan_form");
		});
		call({ action: "my_loans", token: state.token })
			.then(function (d) {
				document.getElementById("panelBody").innerHTML = loansHtml(d);
			})
			.catch(function (err) {
				if (expired(err && err.message)) return;
				document.getElementById("panelBody").innerHTML = emptyBlock(
					"fa-exclamation-triangle",
					"Cannot load loans",
					(err && err.message) || ""
				);
			});
	}

	function loansHtml(d) {
		var advances = d.salary_advances || [];
		var loans = d.employee_loans || [];
		
		if (!advances.length && !loans.length) {
			return emptyBlock("fa-money", "No loans or advances", "Your salary advances and loans will appear here.");
		}
		
		var html = '';
		
		// Summary stats
		html +=
			'<div class="ep-loans-summary" style="display:flex;gap:1rem;flex-wrap:wrap;margin-bottom:1.5rem">' +
			'<span class="ep-badge ep-badge--warn" style="font-size:.9rem;padding:.5rem .8rem"><i class="fa fa-clock-o"></i> ' + esc(d.pending_advances || 0) + ' Pending</span>' +
			'<span class="ep-badge ep-badge--info" style="font-size:.9rem;padding:.5rem .8rem"><i class="fa fa-money"></i> ' + esc(d.active_loans || 0) + ' Active Loans</span>' +
			'<span class="ep-badge ep-badge--bad" style="font-size:.9rem;padding:.5rem .8rem"><i class="fa fa-exclamation-triangle"></i> ' + esc(d.total_outstanding || 0) + ' Outstanding</span>' +
			'</div>';
		
		// Salary Advances
		if (advances.length) {
			html += '<p class="ep-card-title"><i class="fa fa-bolt"></i> Salary Advances</p>';
			advances.forEach(function (a) {
				var statusClass = a.status === "Paid" ? "ep-badge--good" : a.status === "Submitted" || a.status === "Pending" ? "ep-badge--warn" : "ep-badge";
				html +=
					'<div class="ep-loan-row" style="border:1px solid var(--ep-line);border-radius:.8rem;padding:1rem;margin-bottom:.75rem;background:var(--ep-card)">' +
					'<div style="display:flex;gap:1rem;align-items:center;flex-wrap:wrap;justify-content:space-between">' +
					'<div style="flex:1;min-width:250px">' +
					'<div style="display:flex;align-items:center;gap:.5rem;margin-bottom:.5rem">' +
					'<strong style="font-size:1.1rem">Salary Advance</strong>' +
					'<span class="ep-badge ' + statusClass + '">' + esc(a.status) + '</span>' +
					'</div>' +
					'<div class="ep-row-meta"><i class="fa fa-money"></i> Amount: ' + esc(a.advance_amount) + ' &middot; Paid: ' + esc(a.paid_amount) + ' &middot; Balance: ' + esc(a.balance_amount) + '</div>' +
					'<div class="ep-row-meta"><i class="fa fa-calendar"></i> ' + esc(dateOnly(a.posting_date)) + (a.deduction_per_month ? ' &middot; ' + esc(a.deduction_per_month) + '/month for ' + esc(a.repay_months) + ' months' : '') + '</div>' +
					(a.purpose ? '<div class="ep-row-meta" style="margin-top:.25rem"><i class="fa fa-comment-o"></i> ' + esc(a.purpose) + '</div>' : '') +
					'</div>' +
					'</div>' +
					'</div>';
			});
		}
		
		// Employee Loans
		if (loans.length) {
			html += '<p class="ep-card-title" style="margin-top:1.5rem"><i class="fa fa-university"></i> Employee Loans</p>';
			loans.forEach(function (l) {
				var statusClass = l.status === "Paid" || l.status === "Closed" ? "ep-badge--good" : l.status === "Disbursed" || l.status === "Partially Paid" ? "ep-badge--info" : l.status === "Pending" ? "ep-badge--warn" : "ep-badge";
				html +=
					'<div class="ep-loan-row" style="border:1px solid var(--ep-line);border-radius:.8rem;padding:1rem;margin-bottom:.75rem;background:var(--ep-card)">' +
					'<div style="display:flex;gap:1rem;align-items:center;flex-wrap:wrap;justify-content:space-between">' +
					'<div style="flex:1;min-width:250px">' +
					'<div style="display:flex;align-items:center;gap:.5rem;margin-bottom:.5rem">' +
					'<strong style="font-size:1.1rem">' + esc(l.loan_type) + '</strong>' +
					'<span class="ep-badge ' + statusClass + '">' + esc(l.status) + '</span>' +
					'</div>' +
					'<div class="ep-row-meta"><i class="fa fa-money"></i> Amount: ' + esc(l.loan_amount) + ' &middot; Paid: ' + esc(l.total_paid_amount || l.total_amount_paid) + ' &middot; Balance: ' + esc(l.balance_amount) + '</div>' +
					'<div class="ep-row-meta"><i class="fa fa-percent"></i> Interest: ' + esc(l.interest_rate || 0) + '% &middot; ' + esc(l.repayment_periods) + ' months &middot; ' + esc(l.repayment_amount) + '/month</div>' +
					'<div class="ep-row-meta"><i class="fa fa-calendar"></i> ' + esc(dateOnly(l.posting_date)) + '</div>' +
					(l.purpose ? '<div class="ep-row-meta" style="margin-top:.25rem"><i class="fa fa-comment-o"></i> ' + esc(l.purpose) + '</div>' : '') +
					'</div>' +
					'</div>' +
					'</div>';
			});
		}
		
		return html;
	}

	function renderLoanForm() {
		host.innerHTML =
			'<div class="ep-fade">' +
			'<button type="button" class="ep-back" id="formBack"><i class="fa fa-arrow-left"></i> Back</button>' +
			'<div class="ep-card" id="panelBody"><div class="ep-empty"><span class="ep-spin"></span></div></div>' +
			"</div>";
		document.getElementById("formBack").addEventListener("click", function () {
			show("loans");
		});
		call({ action: "loan_types", token: state.token })
			.then(function (d) {
				var types = (d.loan_types || []).map(function (t) {
					return '<option value="' + esc(t.name) + '" data-rate="' + esc(t.interest_rate || 0) + '" data-max="' + esc(t.max_amount || 0) + '" data-periods="' + esc(t.max_repayment_periods || 12) + '">' + esc(t.loan_type_name || t.name) + (t.interest_rate ? ' (' + esc(t.interest_rate) + '%)' : '') + '</option>';
				}).join('');
				document.getElementById("panelBody").innerHTML =
					'<p class="ep-card-title">Request Loan / Advance</p>' +
					'<form id="loanForm" novalidate>' +
					'<div class="ep-field"><label class="ep-label" for="lnType">Type</label>' +
					'<select class="ep-input" id="lnType" required>' +
					'<option value="Salary Advance">Salary Advance (no interest, repaid via salary)</option>' +
					types +
					'</select></div>' +
					'<div class="ep-field"><label class="ep-label" for="lnAmount">Amount</label>' +
					'<input class="ep-input" id="lnAmount" type="number" step="0.01" min="1" required /></div>' +
					'<div class="ep-field"><label class="ep-label" for="lnPurpose">Purpose</label>' +
					'<textarea class="ep-input" id="lnPurpose" rows="3" placeholder="Why do you need this loan/advance?" required></textarea></div>' +
					'<div style="display:grid;grid-template-columns:1fr 1fr;gap:.75rem">' +
					'<div class="ep-field"><label class="ep-label" for="lnMonths">Repayment Months</label>' +
					'<input class="ep-input" id="lnMonths" type="number" min="1" max="60" value="12" /></div>' +
					'<div class="ep-field"><label class="ep-label" for="lnRate">Interest Rate % (for loans)</label>' +
					'<input class="ep-input" id="lnRate" type="number" step="0.1" min="0" value="0" /></div>' +
					'</div>' +
					'<button class="ep-btn" type="submit">Submit Request</button></form>';
				document.getElementById("loanForm").addEventListener("submit", function (e) {
					e.preventDefault();
					submitLoanForm();
				});
				
				// Auto-fill interest rate and max periods when loan type changes
				document.getElementById("lnType").addEventListener("change", function () {
					var opt = this.options[this.selectedIndex];
					if (opt.dataset.rate) document.getElementById("lnRate").value = opt.dataset.rate;
					if (opt.dataset.periods) document.getElementById("lnMonths").value = opt.dataset.periods;
					if (opt.dataset.max) document.getElementById("lnAmount").max = opt.dataset.max;
				});
			})
			.catch(function (err) {
				if (expired(err && err.message)) return;
				document.getElementById("panelBody").innerHTML = emptyBlock(
					"fa-exclamation-triangle",
					"Cannot load loan types",
					(err && err.message) || ""
				);
			});
	}

	function submitLoanForm() {
		call({
			action: "request_loan",
			token: state.token,
			loan_type: document.getElementById("lnType").value,
			amount: parseFloat(document.getElementById("lnAmount").value),
			purpose: document.getElementById("lnPurpose").value,
			repayment_months: parseInt(document.getElementById("lnMonths").value, 10) || 12,
			interest_rate: parseFloat(document.getElementById("lnRate").value) || 0,
		})
			.then(function () {
				announce("Loan request submitted.");
				show("loans");
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

	function renderWellness() {
		host.innerHTML =
			'<div class="ep-fade">' +
			'<button type="button" class="ep-back" id="panelBack"><i class="fa fa-arrow-left"></i> Back</button>' +
			'<button type="button" class="ep-btn ep-btn-ghost" id="newIncident" style="margin-bottom:.8rem;float:right"><i class="fa fa-plus"></i> Report Incident</button>' +
			'<button type="button" class="ep-btn ep-btn-ghost" id="newObservation" style="margin-bottom:.8rem;float:right;margin-right:.5rem"><i class="fa fa-eye"></i> Report Observation</button>' +
			alertHtml() +
			'<div class="ep-card" id="panelBody"><div class="ep-empty"><span class="ep-spin"></span></div></div>' +
			"</div>";
		document.getElementById("panelBack").addEventListener("click", function () {
			show("dashboard");
		});
		document.getElementById("newIncident").addEventListener("click", function () {
			show("incident_form");
		});
		document.getElementById("newObservation").addEventListener("click", function () {
			show("observation_form");
		});
		call({ action: "my_wellness", token: state.token })
			.then(function (d) {
				document.getElementById("panelBody").innerHTML = wellnessHtml(d);
			})
			.catch(function (err) {
				if (expired(err && err.message)) return;
				document.getElementById("panelBody").innerHTML = emptyBlock(
					"fa-exclamation-triangle",
					"Cannot load wellness",
					(err && err.message) || ""
				);
			});
	}

	function wellnessHtml(d) {
		var incidents = d.incidents || [];
		var wellness = d.wellness_programs || [];
		var checkups = d.health_checkups || [];
		var observations = d.safety_observations || [];
		
		if (!incidents.length && !wellness.length && !checkups.length && !observations.length) {
			return emptyBlock("fa-heartbeat", "No wellness records", "Your safety incidents, wellness programs, and health checkups will appear here.");
		}
		
		var html = '';
		
		// Summary stats
		html +=
			'<div class="ep-wellness-summary" style="display:flex;gap:1rem;flex-wrap:wrap;margin-bottom:1.5rem">' +
			'<span class="ep-badge ep-badge--warn" style="font-size:.9rem;padding:.5rem .8rem"><i class="fa fa-exclamation-triangle"></i> ' + esc(d.open_incidents || 0) + ' Open Incidents</span>' +
			'<span class="ep-badge ep-badge--info" style="font-size:.9rem;padding:.5rem .8rem"><i class="fa fa-leaf"></i> ' + esc(d.completed_programs || 0) + ' Programs Done</span>' +
			'<span class="ep-badge ep-badge--good" style="font-size:.9rem;padding:.5rem .8rem"><i class="fa fa-stethoscope"></i> ' + esc(d.upcoming_checkups || 0) + ' Checkups Due</span>' +
			'<span class="ep-badge" style="font-size:.9rem;padding:.5rem .8rem;background:color-mix(in srgb, var(--ep-muted) 15%, transparent);color:var(--ep-muted)"><i class="fa fa-eye"></i> ' + esc(observations.length) + ' Observations</span>' +
			'</div>';
		
		// Safety Incidents
		if (incidents.length) {
			html += '<p class="ep-card-title"><i class="fa fa-exclamation-triangle"></i> Safety Incidents</p>';
			incidents.forEach(function (i) {
				var severityClass = i.severity === "Critical" ? "ep-badge--bad" : i.severity === "High" ? "ep-badge--bad" : i.severity === "Medium" ? "ep-badge--warn" : "ep-badge--info";
				var statusClass = i.status === "Closed" ? "ep-badge--good" : i.status === "Resolved" ? "ep-badge--info" : i.status === "Under Investigation" ? "ep-badge--warn" : "ep-badge";
				html +=
					'<div class="ep-wellness-row" style="border:1px solid var(--ep-line);border-radius:.8rem;padding:1rem;margin-bottom:.75rem;background:var(--ep-card)">' +
					'<div style="display:flex;gap:1rem;align-items:center;flex-wrap:wrap;justify-content:space-between">' +
					'<div style="flex:1;min-width:250px">' +
					'<div style="display:flex;align-items:center;gap:.5rem;margin-bottom:.5rem">' +
					'<strong style="font-size:1.1rem">' + esc(i.incident_type) + '</strong>' +
					'<span class="ep-badge ' + severityClass + '">' + esc(i.severity) + '</span>' +
					'<span class="ep-badge ' + statusClass + '">' + esc(i.status) + '</span>' +
					'</div>' +
					'<div class="ep-row-meta"><i class="fa fa-calendar"></i> ' + esc(dateOnly(i.incident_date)) + ' &middot; <i class="fa fa-map-marker"></i> ' + esc(i.location) + '</div>' +
					'<div class="ep-row-meta">' + esc(i.description) + '</div>' +
					(i.root_cause ? '<div class="ep-row-meta" style="margin-top:.25rem"><i class="fa fa-search"></i> Root Cause: ' + esc(i.root_cause) + '</div>' : '') +
					(i.corrective_action ? '<div class="ep-row-meta" style="margin-top:.25rem"><i class="fa fa-wrench"></i> Action: ' + esc(i.corrective_action) + '</div>' : '') +
					'</div>' +
					'</div>' +
					'</div>';
			});
		}
		
		// Safety Observations
		if (observations.length) {
			html += '<p class="ep-card-title" style="margin-top:1.5rem"><i class="fa fa-eye"></i> Safety Observations</p>';
			observations.forEach(function (o) {
				var severityClass = o.severity === "Critical" ? "ep-badge--bad" : o.severity === "High" ? "ep-badge--bad" : o.severity === "Medium" ? "ep-badge--warn" : "ep-badge--info";
				var statusClass = o.status === "Closed" ? "ep-badge--good" : o.status === "Action Taken" ? "ep-badge--info" : "ep-badge--warn";
				html +=
					'<div class="ep-wellness-row" style="border:1px solid var(--ep-line);border-radius:.8rem;padding:1rem;margin-bottom:.75rem;background:var(--ep-card)">' +
					'<div style="display:flex;gap:1rem;align-items:center;flex-wrap:wrap;justify-content:space-between">' +
					'<div style="flex:1;min-width:250px">' +
					'<div style="display:flex;align-items:center;gap:.5rem;margin-bottom:.5rem">' +
					'<strong style="font-size:1.1rem">' + esc(o.observation_type) + '</strong>' +
					'<span class="ep-badge ' + severityClass + '">' + esc(o.severity) + '</span>' +
					'<span class="ep-badge ' + statusClass + '">' + esc(o.status) + '</span>' +
					'</div>' +
					'<div class="ep-row-meta"><i class="fa fa-calendar"></i> ' + esc(dateOnly(o.observation_date)) + ' &middot; <i class="fa fa-map-marker"></i> ' + esc(o.location) + '</div>' +
					'<div class="ep-row-meta">' + esc(o.description) + '</div>' +
					(o.suggested_action ? '<div class="ep-row-meta" style="margin-top:.25rem"><i class="fa fa-lightbulb-o"></i> Suggested: ' + esc(o.suggested_action) + '</div>' : '') +
					'</div>' +
					'</div>' +
					'</div>';
			});
		}
		
		// Wellness Programs
		if (wellness.length) {
			html += '<p class="ep-card-title" style="margin-top:1.5rem"><i class="fa fa-leaf"></i> Wellness Programs</p>';
			wellness.forEach(function (w) {
				var statusClass = w.status === "Completed" ? "ep-badge--good" : w.status === "In Progress" ? "ep-badge--info" : "ep-badge--warn";
				var progress = w.progress || 0;
				html +=
					'<div class="ep-wellness-row" style="border:1px solid var(--ep-line);border-radius:.8rem;padding:1rem;margin-bottom:.75rem;background:var(--ep-card)">' +
					'<div style="display:flex;gap:1rem;align-items:center;flex-wrap:wrap;justify-content:space-between">' +
					'<div style="flex:1;min-width:250px">' +
					'<div class="ep-row-title">' + esc(w.program_name) + '</div>' +
					'<div class="ep-row-meta">' + esc(dateOnly(w.start_date)) + (w.end_date ? ' &rarr; ' + esc(dateOnly(w.end_date)) : '') + '</div>' +
					(w.description ? '<div class="ep-row-meta" style="margin-top:.25rem">' + esc(w.description) + '</div>' : '') +
					'</div>' +
					'<div style="text-align:right;min-width:150px">' +
					'<div class="ep-badge ' + statusClass + '" style="margin-bottom:.5rem">' + esc(w.status) + '</div>' +
					'<div style="width:120px;height:8px;background:color-mix(in srgb, var(--ep-muted) 15%, transparent);border-radius:4px;overflow:hidden">' +
					'<div style="width:' + progress + '%;height:100%;background:var(--ep-accent);transition:width .3s"></div>' +
					'</div>' +
					'<div style="font-size:.8rem;color:var(--ep-muted);margin-top:.25rem">' + progress + '% complete</div>' +
					'</div>' +
					'</div>' +
					'</div>';
			});
		}
		
		// Health Checkups
		if (checkups.length) {
			html += '<p class="ep-card-title" style="margin-top:1.5rem"><i class="fa fa-stethoscope"></i> Health Checkups</p>';
			checkups.forEach(function (c) {
				var statusClass = c.status === "Completed" ? "ep-badge--good" : c.status === "Pending Results" ? "ep-badge--warn" : "ep-badge--info";
				var isDue = c.next_due_date && c.next_due_date >= new Date().toISOString().split("T")[0];
				html +=
					'<div class="ep-wellness-row" style="border:1px solid var(--ep-line);border-radius:.8rem;padding:1rem;margin-bottom:.75rem;background:var(--ep-card)"' + (isDue ? ' style="border-left:4px solid var(--ep-accent)"' : '') + '>' +
					'<div style="display:flex;gap:1rem;align-items:center;flex-wrap:wrap;justify-content:space-between">' +
					'<div style="flex:1;min-width:250px">' +
					'<div style="display:flex;align-items:center;gap:.5rem;margin-bottom:.5rem">' +
					'<strong style="font-size:1.1rem">' + esc(c.checkup_type) + '</strong>' +
					'<span class="ep-badge ' + statusClass + '">' + esc(c.status) + '</span>' +
					(isDue ? '<span class="ep-badge ep-badge--info" style="font-size:.7rem"><i class="fa fa-calendar"></i> Due Soon</span>' : '') +
					'</div>' +
					'<div class="ep-row-meta"><i class="fa fa-calendar"></i> ' + esc(dateOnly(c.checkup_date)) + (c.provider ? ' &middot; <i class="fa fa-building"></i> ' + esc(c.provider) : '') + '</div>' +
					(c.next_due_date ? '<div class="ep-row-meta"><i class="fa fa-clock-o"></i> Next Due: ' + esc(dateOnly(c.next_due_date)) + '</div>' : '') +
					(c.results_summary ? '<div class="ep-row-meta" style="margin-top:.25rem"><i class="fa fa-file-text-o"></i> ' + esc(c.results_summary) + '</div>' : '') +
					'</div>' +
					'</div>' +
					'</div>';
			});
		}
		
		return html;
	}

	function renderRecognition() {
		host.innerHTML =
			'<div class="ep-fade">' +
			'<button type="button" class="ep-back" id="panelBack"><i class="fa fa-arrow-left"></i> Back</button>' +
			'<button type="button" class="ep-btn ep-btn-ghost" id="newRecognition" style="margin-bottom:.8rem;float:right"><i class="fa fa-plus"></i> Give Recognition</button>' +
			alertHtml() +
			'<div class="ep-card" id="panelBody"><div class="ep-empty"><span class="ep-spin"></span></div></div>' +
			"</div>";
		document.getElementById("panelBack").addEventListener("click", function () {
			show("dashboard");
		});
		document.getElementById("newRecognition").addEventListener("click", function () {
			show("recognition_form");
		});
		call({ action: "my_recognition", token: state.token })
			.then(function (d) {
				document.getElementById("panelBody").innerHTML = recognitionHtml(d);
			})
			.catch(function (err) {
				if (expired(err && err.message)) return;
				document.getElementById("panelBody").innerHTML = emptyBlock(
					"fa-exclamation-triangle",
					"Cannot load recognition",
					(err && err.message) || ""
				);
			});
	}

	function recognitionHtml(d) {
		var received = d.received || [];
		var sent = d.sent || [];
		var badges = d.badges || [];
		
		if (!received.length && !sent.length && !badges.length) {
			return emptyBlock("fa-trophy", "No recognition yet", "Give and receive recognition from your colleagues.");
		}
		
		var html = '';
		
		// Summary stats
		html +=
			'<div class="ep-recognition-summary" style="display:flex;gap:1rem;flex-wrap:wrap;margin-bottom:1.5rem">' +
			'<span class="ep-badge ep-badge--good" style="font-size:.9rem;padding:.5rem .8rem"><i class="fa fa-trophy"></i> ' + esc(d.points_balance || 0) + ' Points</span>' +
			'<span class="ep-badge ep-badge--info" style="font-size:.9rem;padding:.5rem .8rem"><i class="fa fa-inbox"></i> ' + esc(d.total_received || 0) + ' Received</span>' +
			'<span class="ep-badge ep-badge--warn" style="font-size:.9rem;padding:.5rem .8rem"><i class="fa fa-paper-plane"></i> ' + esc(d.total_sent || 0) + ' Sent</span>' +
			'<span class="ep-badge" style="font-size:.9rem;padding:.5rem .8rem;background:color-mix(in srgb, var(--ep-muted) 15%, transparent);color:var(--ep-muted)"><i class="fa fa-medal"></i> ' + esc(badges.length) + ' Badges</span>' +
			'</div>';
		
		// Badges
		if (badges.length) {
			html += '<p class="ep-card-title"><i class="fa fa-medal"></i> Badges Earned</p>';
			html += '<div class="ep-badges-grid" style="display:grid;grid-template-columns:repeat(auto-fill,minmax(140px,1fr));gap:1rem;margin-bottom:1.5rem">';
			badges.forEach(function (b) {
				html +=
					'<div class="ep-badge-card" style="border:1px solid var(--ep-line);border-radius:.8rem;padding:1rem;background:var(--ep-card);text-align:center">' +
					'<div style="width:4rem;height:4rem;border-radius:50%;margin:0 auto .75rem;background:' + esc(b.color || '#f59e0b') + ';display:flex;align-items:center;justify-content:center">' +
					'<i class="fa ' + esc(b.icon || 'fa-trophy') + '" style="font-size:1.5rem;color:white"></i>' +
					'</div>' +
					'<div class="ep-row-title" style="font-size:.9rem">' + esc(b.badge_name) + '</div>' +
					'<div class="ep-row-meta" style="font-size:.75rem">' + esc(b.badge_type) + '</div>' +
					'<div class="ep-row-meta" style="font-size:.7rem;margin-top:.25rem">' + esc(dateOnly(b.earned_date)) + '</div>' +
					(b.description ? '<div class="ep-row-meta" style="font-size:.7rem;margin-top:.25rem;color:var(--ep-muted)">' + esc(b.description) + '</div>' : '') +
					'</div>';
			});
			html += '</div>';
		}
		
		// Received Recognition
		if (received.length) {
			html += '<p class="ep-card-title" style="margin-top:1.5rem"><i class="fa fa-inbox"></i> Received (' + received.length + ')</p>';
			received.forEach(function (r) {
				html +=
					'<div class="ep-recognition-card" style="border:1px solid var(--ep-line);border-radius:.8rem;padding:1rem;margin-bottom:.75rem;background:var(--ep-card);border-left:4px solid var(--ep-good)">' +
					'<div style="display:flex;gap:1rem;align-items:flex-start;flex-wrap:wrap;justify-content:space-between">' +
					'<div style="flex:1;min-width:250px">' +
					'<div style="display:flex;align-items:center;gap:.5rem;margin-bottom:.5rem">' +
					'<strong style="font-size:1.1rem">' + esc(r.recognition_type) + '</strong>' +
					(r.points > 0 ? '<span class="ep-badge ep-badge--info" style="font-size:.7rem"><i class="fa fa-star"></i> +' + esc(r.points) + '</span>' : '') +
					(r.badge ? '<span class="ep-badge" style="font-size:.7rem;background:color-mix(in srgb, var(--ep-warn) 15%, transparent);color:var(--ep-warn)"><i class="fa fa-medal"></i> ' + esc(r.badge) + '</span>' : '') +
					'</div>' +
					'<div class="ep-row-meta">From: ' + esc(r.sender_name) + ' &middot; ' + esc(dateOnly(r.recognition_date)) + '</div>' +
					'<div class="ep-row-meta" style="margin-top:.25rem;line-height:1.5">' + esc(r.message) + '</div>' +
					'</div>' +
					'</div>' +
					'</div>';
			});
		}
		
		// Sent Recognition
		if (sent.length) {
			html += '<p class="ep-card-title" style="margin-top:1.5rem"><i class="fa fa-paper-plane"></i> Sent (' + sent.length + ')</p>';
			sent.forEach(function (s) {
				html +=
					'<div class="ep-recognition-card" style="border:1px solid var(--ep-line);border-radius:.8rem;padding:1rem;margin-bottom:.75rem;background:var(--ep-card);border-left:4px solid var(--ep-accent)">' +
					'<div style="display:flex;gap:1rem;align-items:flex-start;flex-wrap:wrap;justify-content:space-between">' +
					'<div style="flex:1;min-width:250px">' +
					'<div style="display:flex;align-items:center;gap:.5rem;margin-bottom:.5rem">' +
					'<strong style="font-size:1.1rem">' + esc(s.recognition_type) + '</strong>' +
					(s.points > 0 ? '<span class="ep-badge ep-badge--info" style="font-size:.7rem"><i class="fa fa-star"></i> +' + esc(s.points) + '</span>' : '') +
					(s.badge ? '<span class="ep-badge" style="font-size:.7rem;background:color-mix(in srgb, var(--ep-warn) 15%, transparent);color:var(--ep-warn)"><i class="fa fa-medal"></i> ' + esc(s.badge) + '</span>' : '') +
					'</div>' +
					'<div class="ep-row-meta">To: ' + esc(s.recipient_name) + ' &middot; ' + esc(dateOnly(s.recognition_date)) + '</div>' +
					'<div class="ep-row-meta" style="margin-top:.25rem;line-height:1.5">' + esc(s.message) + '</div>' +
					'</div>' +
					'</div>' +
					'</div>';
			});
		}
		
		return html;
	}

	function renderRecognitionForm() {
		host.innerHTML =
			'<div class="ep-fade">' +
			'<button type="button" class="ep-back" id="formBack"><i class="fa fa-arrow-left"></i> Back</button>' +
			'<div class="ep-card" id="panelBody"><div class="ep-empty"><span class="ep-spin"></span></div></div>' +
			"</div>";
		document.getElementById("formBack").addEventListener("click", function () {
			show("recognition");
		});
		call({ action: "recognition_types", token: state.token })
			.then(function (d) {
				var types = (d.recognition_types || []).map(function (t) {
					return '<option value="' + esc(t.name) + '" data-points="' + esc(t.default_points || 0) + '">' + esc(t.type_name || t.name) + (t.default_points ? ' (+' + esc(t.default_points) + ' pts)' : '') + '</option>';
				}).join('');
				
				call({ action: "my_colleagues", token: state.token })
					.then(function (colleagues) {
						var colleagueOptions = (colleagues.colleagues || [])
							.filter(function (c) { return c.name !== state.employee; })
							.map(function (c) {
								return '<option value="' + esc(c.name) + '">' + esc(c.employee_name) + ' (' + esc(c.employee_number) + ')</option>';
							}).join('');
						
						document.getElementById("panelBody").innerHTML =
							'<p class="ep-card-title">Give Recognition</p>' +
							'<form id="recognitionForm" novalidate>' +
							'<div class="ep-field"><label class="ep-label" for="recRecipient">Recipient</label>' +
							'<select class="ep-input" id="recRecipient" required>' +
							'<option value="">Select a colleague</option>' + colleagueOptions +
							'</select></div>' +
							'<div class="ep-field"><label class="ep-label" for="recType">Type</label>' +
							'<select class="ep-input" id="recType" required>' +
							'<option value="">Select type</option>' + types +
							'</select></div>' +
							'<div class="ep-field"><label class="ep-label" for="recMessage">Message</label>' +
							'<textarea class="ep-input" id="recMessage" rows="4" placeholder="Why are you recognizing this person? Be specific!" required></textarea></div>' +
							'<div style="display:grid;grid-template-columns:1fr 1fr;gap:.75rem">' +
							'<div class="ep-field"><label class="ep-label" for="recPoints">Points</label>' +
							'<input class="ep-input" id="recPoints" type="number" min="0" value="0" /></div>' +
							'<div class="ep-field"><label class="ep-label" for="recBadge">Badge (optional)</label>' +
							'<input class="ep-input" id="recBadge" placeholder="e.g., Team Player, Innovation Star" /></div>' +
							'</div>' +
							'<button class="ep-btn" type="submit">Send Recognition</button></form>';
						
						document.getElementById("recognitionForm").addEventListener("submit", function (e) {
							e.preventDefault();
							submitRecognitionForm();
						});
						
						// Auto-fill points when type changes
						document.getElementById("recType").addEventListener("change", function () {
							var opt = this.options[this.selectedIndex];
							if (opt.dataset.points) document.getElementById("recPoints").value = opt.dataset.points;
						});
					})
					.catch(function (err) {
						if (expired(err && err.message)) return;
						document.getElementById("panelBody").innerHTML = emptyBlock(
							"fa-exclamation-triangle",
							"Cannot load colleagues",
							(err && err.message) || ""
						);
					});
			})
			.catch(function (err) {
				if (expired(err && err.message)) return;
				document.getElementById("panelBody").innerHTML = emptyBlock(
					"fa-exclamation-triangle",
					"Cannot load types",
					(err && err.message) || ""
				);
			});
	}

	function submitRecognitionForm() {
		call({
			action: "send_recognition",
			token: state.token,
			recipient: document.getElementById("recRecipient").value,
			recognition_type: document.getElementById("recType").value,
			message: document.getElementById("recMessage").value,
			points: parseInt(document.getElementById("recPoints").value) || 0,
			badge: document.getElementById("recBadge").value || null,
		})
			.then(function () {
				announce("Recognition sent!");
				show("recognition");
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

	function renderSupport() {
		host.innerHTML =
			'<div class="ep-fade">' +
			'<button type="button" class="ep-back" id="panelBack"><i class="fa fa-arrow-left"></i> Back</button>' +
			alertHtml() +
			'<div class="ep-card" id="panelBody"><div class="ep-empty"><span class="ep-spin"></span></div></div>' +
			"</div>";
		document.getElementById("panelBack").addEventListener("click", function () {
			show("dashboard");
		});
		call({ action: "my_support", token: state.token })
			.then(function (d) {
				document.getElementById("panelBody").innerHTML = supportHtml(d);
			})
			.catch(function (err) {
				if (expired(err && err.message)) return;
				document.getElementById("panelBody").innerHTML = emptyBlock(
					"fa-exclamation-triangle",
					"Cannot load support",
					(err && err.message) || ""
				);
			});
	}

	function supportHtml(d) {
		var html = '<p class="ep-card-title">Help & Support</p>';
		var hasContent = false;

		// HR Contacts
		var hr = d.hr_contacts || [];
		if (hr.length) {
			hasContent = true;
			html += '<p class="ep-card-title" style="margin-top:1rem"><i class="fa fa-user-tie"></i> HR Contacts</p>';
			html += '<div class="ep-support-grid">';
			hr.forEach(function (c) {
				var initials = (c.employee_name || "?").split(" ").map(function (w) { return w[0]; }).join("").substring(0, 2).toUpperCase();
				var avatar = c.image
					? '<img src="' + esc(c.image) + '" alt="" style="width:100%;height:100%;object-fit:cover;border-radius:50%">'
					: '<div style="width:100%;height:100%;display:flex;align-items:center;justify-content:center;border-radius:50%;background:linear-gradient(135deg,var(--ep-accent),var(--ep-accent-dark));color:var(--ep-accent-ink);font-weight:700;font-size:1rem">' + esc(initials) + '</div>';
				var email = c.company_email ? '<a href="mailto:' + esc(c.company_email) + '" style="color:var(--ep-accent);text-decoration:none"><i class="fa fa-envelope-o"></i> ' + esc(c.company_email) + '</a>' : '';
				var phone = c.cell_number ? '<a href="tel:' + esc(c.cell_number) + '" style="color:var(--ep-accent);text-decoration:none;margin-left:1rem"><i class="fa fa-phone"></i> ' + esc(c.cell_number) + '</a>' : '';
				var dept = c.department ? '<div class="ep-row-meta"><i class="fa fa-building-o"></i> ' + esc(c.department) + '</div>' : '';
				html +=
					'<div class="ep-support-card" style="border:1px solid var(--ep-line);border-radius:.8rem;padding:1rem;background:var(--ep-card)">' +
					'<div style="display:flex;gap:1rem;align-items:center">' +
					'<div style="width:3.5rem;height:3.5rem;border-radius:50%;overflow:hidden;flex-shrink:0">' + avatar + '</div>' +
					'<div style="flex:1;min-width:0">' +
					'<div class="ep-row-title" style="font-size:.95rem">' + esc(c.employee_name) + '</div>' +
					'<div class="ep-row-meta">' + esc(c.designation) + '</div>' +
					dept +
					'<div style="margin-top:.5rem">' + email + phone + '</div>' +
					'</div>' +
					'</div>' +
					'</div>';
			});
			html += '</div>';
		}

		// FAQs
		var faqs = d.faqs || [];
		if (faqs.length) {
			hasContent = true;
			html += '<p class="ep-card-title" style="margin-top:1.5rem"><i class="fa fa-question-circle"></i> Frequently Asked Questions</p>';
			faqs.forEach(function (f) {
				html +=
					'<details class="ep-faq-item" style="border:1px solid var(--ep-line);border-radius:.6rem;overflow:hidden;margin-bottom:.5rem;background:var(--ep-card)">' +
					'<summary style="padding:1rem;cursor:pointer;font-weight:600;list-style:none;display:flex;align-items:center;gap:.5rem">' +
					'<i class="fa fa-chevron-right" style="color:var(--ep-muted);transition:transform .2s"></i>' +
					esc(f.title) +
					'</summary>' +
					'<div style="padding:0 1rem 1rem;border-top:1px solid var(--ep-line);line-height:1.6">' + f.content + '</div>' +
					'</details>';
			});
		}

		// Policies
		var policies = d.policies || [];
		if (policies.length) {
			hasContent = true;
			html += '<p class="ep-card-title" style="margin-top:1.5rem"><i class="fa fa-file-text-o"></i> Policies & Documents</p>';
			policies.forEach(function (p) {
				var attachment = p.attachment ? '<a href="' + esc(p.attachment) + '" target="_blank" style="color:var(--ep-accent);text-decoration:none;margin-left:.75rem"><i class="fa fa-download"></i> Download</a>' : '';
				html +=
					'<div class="ep-policy-item" style="border:1px solid var(--ep-line);border-radius:.6rem;padding:1rem;margin-bottom:.5rem;background:var(--ep-card)">' +
					'<div style="display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:.5rem;margin-bottom:.5rem">' +
					'<strong>' + esc(p.title) + '</strong>' +
					attachment +
					'</div>' +
					'<div style="line-height:1.6;color:var(--ep-ink)">' + p.content + '</div>' +
					'</div>';
			});
		}

		// Help topics
		var help = d.help_topics || [];
		if (help.length) {
			hasContent = true;
			html += '<p class="ep-card-title" style="margin-top:1.5rem"><i class="fa fa-lightbulb-o"></i> Help Topics</p>';
			help.forEach(function (h) {
				html +=
					'<details class="ep-help-item" style="border:1px solid var(--ep-line);border-radius:.6rem;overflow:hidden;margin-bottom:.5rem;background:var(--ep-card)">' +
					'<summary style="padding:1rem;cursor:pointer;font-weight:600;list-style:none;display:flex;align-items:center;gap:.5rem">' +
					'<i class="fa fa-chevron-right" style="color:var(--ep-muted);transition:transform .2s"></i>' +
					esc(h.title) +
					'</summary>' +
					'<div style="padding:0 1rem 1rem;border-top:1px solid var(--ep-line);line-height:1.6">' + h.content + '</div>' +
					'</details>';
			});
		}

		if (!hasContent) {
			html = emptyBlock("fa-life-ring", "No support content yet", "HR can add FAQs, policies, and help topics from the desk.");
		}

		return html;
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
		initPWA();

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

	function initPWA() {
		// Listen for SW messages (version updates)
		if ('serviceWorker' in navigator) {
			navigator.serviceWorker.addEventListener('message', function(event) {
				if (event.data && event.data.version) {
					console.log('SW version:', event.data.version);
				}
			});
		}

		// Handle offline API responses
		var originalCall = window.call;
		window.call = function(payload) {
			return originalCall(payload).catch(function(err) {
				if (err && err.message && err.message.includes('Offline')) {
					// Show offline indicator
					var banner = document.getElementById('epOfflineBanner');
					if (banner) banner.hidden = false;
					document.body.classList.add('ep-offline');
				}
				throw err;
			});
		};

		// Check for app updates
		if ('serviceWorker' in navigator) {
			navigator.serviceWorker.ready.then(function(reg) {
				reg.addEventListener('updatefound', function() {
					var newWorker = reg.installing;
					if (newWorker) {
						newWorker.addEventListener('statechange', function() {
							if (newWorker.state === 'installed' && navigator.serviceWorker.controller) {
								// New version available, prompt reload
								if (confirm('A new version of the portal is available. Reload to update?')) {
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