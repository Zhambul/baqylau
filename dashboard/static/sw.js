/* dashboard/static/sw.js — the Web Push service worker (docs/dashboard.md,
   *Web push*). Served at the ROOT path /sw.js (not /static/) so its scope is
   the whole origin — the one reason the server has a bespoke /sw.js route.

   Deliberately minimal: it exists ONLY to receive a push while the installed
   app isn't the foreground tab and raise a system notification, and to focus
   the app when that notification is tapped. It caches nothing and intercepts no
   fetches — the dashboard is a live SSE app, never an offline one. app.js owns
   registration + subscription; the server (dashboard/webpush.py) owns sending. */

// Take control of open pages as soon as a new worker is installed, so a
// refreshed app.js and its worker agree on the same build.
self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (e) => e.waitUntil(self.clients.claim()));

// A push arrives as the JSON the server encrypted (webpush.Notifier._webpush):
// {title, body, sid, url}. userVisibleOnly subscriptions MUST show a
// notification for every push, so a missing/blank payload still surfaces one.
self.addEventListener("push", (e) => {
  let d = {};
  try { d = e.data ? e.data.json() : {}; } catch (_) { d = {}; }
  const title = d.title || "baqylau";
  const body = d.body || "";
  const sid = d.sid || "";
  // update the app-icon badge to the needs-you count carried in the push, so
  // the icon is right even though the app is closed (the app itself keeps the
  // badge live from the sessions snapshot while open). docs/dashboard.md
  // *Installed-app polish*.
  if (typeof d.badge === "number" && "setAppBadge" in self.navigator) {
    (d.badge ? self.navigator.setAppBadge(d.badge) : self.navigator.clearAppBadge())
      .catch(() => {});
  }
  e.waitUntil(self.registration.showNotification(title, {
    body,
    // tag collapses repeat alerts for the same session into one banner (a
    // re-fired asking/done replaces rather than stacks), matching the desktop
    // osNotify tag.
    tag: "claude-" + sid,
    renotify: true,
    data: { url: d.url || "/", sid },
  }));
});

// Tapping the notification focuses an already-open dashboard window (navigating
// it to the deep link) or opens a fresh one. The URL is the same ?s=<sid> deep
// link the Telegram alert uses — app.js translates it into the #/s/<sid> route.
self.addEventListener("notificationclick", (e) => {
  e.notification.close();
  const url = (e.notification.data && e.notification.data.url) || "/";
  e.waitUntil(self.clients.matchAll({ type: "window", includeUncontrolled: true })
    .then((wins) => {
      for (const w of wins) {
        if ("focus" in w) {
          if ("navigate" in w) { try { w.navigate(url); } catch (_) { /* cross-origin */ } }
          return w.focus();
        }
      }
      return self.clients.openWindow(url);
    }));
});
