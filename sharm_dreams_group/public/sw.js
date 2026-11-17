// Copyright (c) 2026, Sharm Dreams Group and contributors
// For license information, see license.txt

// The employee portal's service worker: an app shell that opens on a wall tablet with
// no signal, showing the last data that was read.
//
// Served at `/employee-portal-sw.js` with `Service-Worker-Allowed: /` so its scope is the
// site root. A script served from `/assets/sharm_dreams_group/` can only control that
// directory, which is not where the page is - so it could never intercept the page's own
// requests, and every "offline" strategy below would have been dead code wearing a comment.
//
// Everything it does is gated on `isPortal`: this worker is installed for the whole
// site, and a desk left offline on a cached half-rendered bundle is worse than a desk
// that simply fails to load. Anything that is not the portal is passed straight to the
// network, untouched and uncached. The shared exceptions are the Inter and FontAwesome
// webfonts, which are cached because the portal's text and icons would otherwise fall
// back offline - no desk page, script or API answer is touched.

const CACHE_VERSION = "ep-v37";
const STATIC_CACHE = `${CACHE_VERSION}-static`;
const API_CACHE = `${CACHE_VERSION}-api`;

const PORTAL_PAGE = "/employee-portal";
const API_PATH = "/employee-portal-api";

const SHELL_ASSETS = [
	"/assets/sharm_dreams_group/css/employee_portal.css",
	"/assets/sharm_dreams_group/js/employee_portal.js",
	"/assets/frappe/css/fonts/inter/inter.css",
	"/assets/frappe/css/fonts/inter/InterVariable.woff2",
	"/assets/frappe/css/fonts/fontawesome/font-awesome.min.css",
	"/assets/frappe/css/fonts/fontawesome/fontawesome-webfont.woff2",
	"/assets/frappe/css/fonts/fontawesome/fontawesome-webfont.woff",
	"/assets/sharm_dreams_group/manifest.json",
	"/assets/sharm_dreams_group/images/icon-192.png",
	"/assets/sharm_dreams_group/images/icon-512.png",
	"/assets/sharm_dreams_group/images/logo.png",
];

// The actions whose answers are worth keeping for a tablet that has lost the network:
// reads only. Anything that writes is fetched or refused, never served from a cache,
// because a stale "your leave was submitted" is worse than no answer at all.
const READABLE = new Set([
	"my_profile",
	"my_details",
	"my_balances",
	"my_leaves",
	"my_holidays",
	"my_notifications",
	"my_approvals",
	"leave_types",
	"my_payslips",
	"my_payslip",
	"my_attendance",
	"my_shifts",
	"my_timesheets",
	"my_overtime",
	"my_leave_calendar",
	"my_colleagues",
	"my_org_chart",
	"my_assets",
	"my_expense_claims",
	"my_expense_claim",
	"my_performance",
	"expense_claim_types",
	"activity_types",
]);

// Assets under `/assets` are served with a year-long max-age. Fetching the bare URL
// during install can therefore pick up a stale HTTP-cache copy - including one cached
// by the previous worker - so every precache fetch explicitly reloads from the network.
// `cache.addAll` is also all-or-nothing, and one missing icon must not leave the whole
// shell unregistered; each URL is stored on its own instead.
function precache(cache, url) {
	return fetch(new Request(url, { cache: "reload" })).then((response) => {
		if (!response.ok) throw new Error(`Precache failed: ${response.status} ${url}`);
		return cache.put(url, response);
	});
}

self.addEventListener("install", (event) => {
	event.waitUntil(
		caches
			.open(STATIC_CACHE)
			.then((cache) =>
				Promise.all(
					[PORTAL_PAGE, ...SHELL_ASSETS].map((url) =>
						precache(cache, url).catch(() => undefined)
					)
				)
			)
			.then(() => self.skipWaiting())
	);
});

self.addEventListener("activate", (event) => {
	event.waitUntil(
		caches
			.keys()
			.then((keys) =>
				Promise.all(
					keys
						.filter((key) => key.startsWith("ep-") && !key.startsWith(CACHE_VERSION))
						.map((key) => caches.delete(key))
				)
			)
			.then(() => self.clients.claim())
	);
});

self.addEventListener("fetch", (event) => {
	const request = event.request;
	const url = new URL(request.url);

	// Only the portal, and only same-origin. Everything else on the site is nobody's
	// business here.
	if (url.origin !== self.location.origin || !isPortal(url)) return;

	if (url.pathname === API_PATH) {
		// Only POST carries an action, and a body has to be read to know which.
		// `request.clone().text()` is async, so this is awaited rather than called
		// inline in a listener that cannot await.
		event.respondWith(apiHandler(request));
		return;
	}

	if (request.method !== "GET") return;

	if (isPage(url)) {
		event.respondWith(pageHandler(request));
		return;
	}

	event.respondWith(assetHandler(request));
});

// The server answers `/employee-portal/` with a redirect, which offline cannot happen:
// the cached page has to answer both spellings or a bookmark with a trailing slash dies.
function isPage(url) {
	return url.pathname === PORTAL_PAGE || url.pathname === `${PORTAL_PAGE}/`;
}

function isPortal(url) {
	return (
		isPage(url) ||
		url.pathname === API_PATH ||
		url.pathname.startsWith("/assets/sharm_dreams_group/") ||
		url.pathname.startsWith("/assets/frappe/css/fonts/inter/") ||
		url.pathname.startsWith("/assets/frappe/css/fonts/fontawesome/")
	);
}

// The API is POST-only, and the Cache API stores an entry under the full request -
// method, URL and body. Two answers to two different actions on one URL therefore
// collide on a single POST key, and the second read returns the first screen's data.
// So the cache key is a synthetic GET URL that carries the action and a hash of the
// body: distinct per action, and - since the token is in that body - distinct per
// session, which is what a shared tablet needs.
async function apiHandler(request) {
	let action = "";
	let key = null;
	try {
		const body = await request.clone().text();
		const data = JSON.parse(body || "{}");
		action = data.action || "";
		key = await cacheKey(request.url, body);
	} catch (error) {
		// A body that is not JSON is not something to cache; it goes to the network.
		return fetch(request);
	}

	const cache = await caches.open(API_CACHE);

	if (!READABLE.has(action)) {
		// A write, or an action that is not on the list: online it must be the network,
		// and offline it must fail rather than pretend. Signing out is the exception to
		// "wait for the server": the employee is gone from this device either way, and
		// cached answers must not outlive them on a shared tablet.
		const offline = () =>
			offlineAnswer(
				"You are offline. That change could not be sent - try again when the signal is back."
			);
		if (action === "sign_out") {
			const response = await fetch(request).catch(offline);
			await dropSessionCache().catch(() => undefined);
			return response;
		}
		return fetch(request).catch(offline);
	}

	if (key === null) return fetch(request);

	return fetch(request)
		.then(async (response) => {
			if (response.ok) {
				// Cloned before the body is read, and the clone - not the original - is
				// what goes into the cache: a response body can be consumed once, and
				// reading it first leaves nothing for the page. Awaited because the
				// worker may be stopped as soon as this handler settles.
				const copy = response.clone();
				await cache.put(key, copy).catch(() => undefined);
			}
			return response;
		})
		.catch(async () => {
			const cached = await cache.match(key);
			if (cached) {
				// Headers on a cached `Response` cannot be amended - they are immutable -
				// so the flag is rewritten into the body, which is what the page reads.
				const body = await cached.json();
				body.offline = true;
				return new Response(JSON.stringify(body), {
					status: 200,
					headers: { "Content-Type": "application/json" },
				});
			}
			return offlineAnswer("You are offline, and this screen was never opened here.");
		});
}

async function cacheKey(apiUrl, body) {
	if (!body) return null;
	const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(body));
	const hash = Array.prototype.map
		.call(new Uint8Array(digest), (byte) => byte.toString(16).padStart(2, "0"))
		.join("")
		.slice(0, 32);
	const url = new URL(apiUrl);
	url.searchParams.set("k", hash);
	return new Request(url.toString(), { method: "GET" });
}

// The shape the client reads: `{"message": ...}` from `build_response("json")`. An
// answer that does not have it makes the page throw on `data.message.error`.
function offlineAnswer(message) {
	return new Response(
		JSON.stringify({ message: { error: message }, offline: true }),
		{ status: 503, headers: { "Content-Type": "application/json" } }
	);
}

// The token is what every cached answer is keyed on, so signing out means the answers
// that were reached with it go too. They cannot be listed by token - the key is a hash
// of a body the worker no longer has - so the whole read cache is dropped. That is the
// right trade: it is only ever stale leave balances, and they are re-read on the next
// sign-in.
async function dropSessionCache() {
	const cache = await caches.open(API_CACHE);
	const names = await cache.keys();
	await Promise.all(names.map((request) => cache.delete(request)));
}

async function pageHandler(request) {
	const cache = await caches.open(STATIC_CACHE);
	try {
		const response = await fetch(request);
		if (response.ok) await cache.put(request, response.clone()).catch(() => undefined);
		return response;
	} catch (error) {
		const fallback = (await cache.match(request)) || (await cache.match(PORTAL_PAGE));
		if (fallback) return fallback;
		// A Response, not the caught error: returning anything else from the handler
		// turns a missing shell into a worker error on top of the offline page.
		return new Response("Offline, and this page was not cached.", {
			status: 503,
			headers: { "Content-Type": "text/plain; charset=utf-8" },
		});
	}
}

async function assetHandler(request) {
	const cache = await caches.open(STATIC_CACHE);
	const cached = await cache.match(request);
	if (cached) return cached;
	try {
		const response = await fetch(request);
		if (response.ok) await cache.put(request, response.clone()).catch(() => undefined);
		return response;
	} catch (error) {
		// The shell is precached without the ?v= the page appends, so an offline
		// first load only matches its stylesheet and script when the query is ignored.
		return (await cache.match(request, { ignoreSearch: true })) || Response.error();
	}
}

self.addEventListener("message", (event) => {
	if (!event.data || typeof event.data !== "object") return;
	if (event.data.type === "skipWaiting") self.skipWaiting();
	if (event.data.type === "getVersion" && event.ports && event.ports[0]) {
		event.ports[0].postMessage({ version: CACHE_VERSION });
	}
	if (event.data.type === "clearCache") {
		caches.keys().then((keys) => {
			keys.filter((key) => key.startsWith("ep-")).forEach((key) => caches.delete(key));
		});
	}
});
