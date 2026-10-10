// Service Worker for Employee Portal - Offline-first caching
// Version bump = cache invalidation
const CACHE_VERSION = 'ep-v12';
const STATIC_CACHE = CACHE_VERSION + '-static';
const API_CACHE = CACHE_VERSION + '-api';
const IMAGE_CACHE = CACHE_VERSION + '-images';

// Assets to cache on install (app shell)
const STATIC_ASSETS = [
	'/employee-portal',
	'/assets/sharm_dreams_group/css/employee_portal.css',
	'/assets/sharm_dreams_group/js/employee_portal.js',
	'/assets/frappe/css/fonts/fontawesome/font-awesome.min.css',
	'/assets/sharm_dreams_group/images/logo.png',
	'/employee-portal-api', // API endpoint (will be cached on first request)
];

// API endpoints to cache for offline reading
const API_ENDPOINTS = [
	'my_profile',
	'my_balances',
	'my_leaves',
	'my_payslips',
	'my_payslip',
	'my_documents',
	'my_announcements',
	'my_colleagues',
	'my_org_chart',
	'my_timesheets',
	'my_overtime',
	'my_leave_calendar',
	'my_support',
	'my_notifications',
	'my_details',
	'leave_types',
	'my_holidays',
	'my_shifts',
	'my_attendance',
	'my_performance',
	'expense_claim_types',
	'activity_types',
];

// Install: cache app shell
self.addEventListener('install', (event) => {
	event.waitUntil(
		caches.open(STATIC_CACHE).then((cache) => {
			return cache.addAll(STATIC_ASSETS.map((url) => new Request(url, { credentials: 'same-origin' })));
		}).then(() => self.skipWaiting())
	);
});

// Activate: clean old caches
self.addEventListener('activate', (event) => {
	event.waitUntil(
		caches.keys().then((keys) => {
			return Promise.all(
				keys.filter((key) => key.startsWith('ep-') && key !== STATIC_CACHE && key !== API_CACHE && key !== IMAGE_CACHE)
					.map((key) => caches.delete(key))
			);
		}).then(() => self.clients.claim())
	);
});

// Fetch: network-first for API, cache-first for static assets
self.addEventListener('fetch', (event) => {
	const url = new URL(event.request.url);
	const isApiCall = url.pathname.includes('/employee-portal-api');
	const isStaticAsset = url.pathname.startsWith('/assets/');
	const isImage = url.pathname.match(/\.(png|jpg|jpeg|svg|gif|webp|ico)$/i);

	// Skip non-GET requests
	if (event.request.method !== 'GET') return;

	// Skip cross-origin requests
	if (url.origin !== location.origin) return;

	// API calls: network-first with cache fallback
	if (isApiCall) {
		event.respondWith(networkFirstApi(event.request));
		return;
	}

	// Static assets & images: cache-first
	if (isStaticAsset || isImage) {
		event.respondWith(cacheFirst(event.request, isImage ? IMAGE_CACHE : STATIC_CACHE));
		return;
	}

	// HTML pages: network-first with offline fallback
	if (event.request.mode === 'navigate' || event.request.headers.get('accept')?.includes('text/html')) {
		event.respondWith(networkFirstHtml(event.request));
		return;
	}

	// Default: network-first
	event.respondWith(networkFirst(event.request));
});

// Network-first strategy for API
async function networkFirstApi(request) {
	const cache = await caches.open(API_CACHE);
	try {
		const response = await fetch(request);
		if (response.ok) {
			// Only cache successful GET responses for read actions
			const cloned = response.clone();
			const body = await cloned.json();
			// Don't cache mutations (POST with action that changes state)
			if (!isMutation(request)) {
				cache.put(request, cloned);
			}
		}
		return response;
	} catch (error) {
		// Offline: try cache
		const cached = await cache.match(request);
		if (cached) {
			return cached;
		}
		// Return offline response
		return new Response(JSON.stringify({
			error: 'Offline',
			message: 'You are offline. Showing cached data.',
			offline: true
		}), {
			status: 503,
			headers: { 'Content-Type': 'application/json' }
		});
	}
}

// Cache-first strategy for static assets
async function cacheFirst(request, cacheName) {
	const cache = await caches.open(cacheName);
	const cached = await cache.match(request);
	if (cached) return cached;

	try {
		const response = await fetch(request);
		if (response.ok) {
			cache.put(request, response.clone());
		}
		return response;
	} catch (error) {
		// For images, return a placeholder
		if (cacheName === IMAGE_CACHE) {
			return new Response('', { status: 404 });
		}
		throw error;
	}
}

// Network-first for HTML with offline fallback
async function networkFirstHtml(request) {
	const cache = await caches.open(STATIC_CACHE);
	try {
		const response = await fetch(request);
		if (response.ok) {
			cache.put(request, response.clone());
		}
		return response;
	} catch (error) {
		const cached = await cache.match(request);
		if (cached) return cached;
		// Fallback to main page
		const fallback = await cache.match('/employee-portal');
		return fallback || new Response('Offline', { status: 503 });
	}
}

// Generic network-first
async function networkFirst(request) {
	const cache = await caches.open(STATIC_CACHE);
	try {
		const response = await fetch(request);
		if (response.ok) {
			cache.put(request, response.clone());
		}
		return response;
	} catch (error) {
		const cached = await cache.match(request);
		return cached || Response.error();
	}
}

// Check if request is a mutation (POST with write action)
function isMutation(request) {
	try {
		const body = request.body ? await request.clone().text() : '';
		const data = JSON.parse(body);
		const writeActions = [
			'sign_in', 'sign_out', 'request_leave', 'cancel_leave',
			'file_expense_claim', 'withdraw_expense_claim',
			'submit_timesheet', 'submit_overtime',
			'update_my_details', 'save_self_appraisal', 'submit_appraisal',
			'punch', 'approve_compensatory_proposal', 'reject_compensatory_proposal',
			'webauthn_login', 'webauthn_verify'
		];
		return writeActions.includes(data.action);
	} catch {
		return false;
	}
}

// Handle messages from client
self.addEventListener('message', (event) => {
	if (event.data === 'skipWaiting') {
		self.skipWaiting();
	}
	if (event.data === 'getVersion') {
		event.ports[0].postMessage({ version: CACHE_VERSION });
	}
	if (event.data === 'clearCache') {
		caches.keys().then(keys => {
			keys.filter(k => k.startsWith('ep-')).forEach(k => caches.delete(k));
		});
	}
});

// Periodic background sync for API cache refresh (when online)
self.addEventListener('periodicsync', (event) => {
	if (event.tag === 'refresh-api-cache') {
		event.waitUntil(refreshApiCache());
	}
});

async function refreshApiCache() {
	const cache = await caches.open(API_CACHE);
	const requests = await cache.keys();
	for (const request of requests) {
		try {
			const response = await fetch(request);
			if (response.ok) {
				await cache.put(request, response);
			}
		} catch {
			// Ignore failed refreshes
		}
	}
}