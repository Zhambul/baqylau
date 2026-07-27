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

// The tag a session's alert is shown under. MUST agree with
// notify/channels.push_tag — a resolve push closes by tag, so a mismatch here
// leaves the banner up forever.
const tagFor = (sid) => "claude-" + (sid || "");

// Close every notification this session's alert is showing. This is the
// RETRACTION (docs/dashboard.md, *Alert retraction*): the server sends a
// `type:"resolve"` push once the session stops needing you, and the banner goes
// away without you having touched it.
//
// It deliberately shows NOTHING afterwards, which is the one place this worker
// knowingly bends the userVisibleOnly contract an iOS subscription is made
// under. WebKit may answer a push that raises no notification with a generic
// placeholder, and can revoke the subscription if it becomes a habit. Two
// things keep that survivable: the server sends at most ONE resolve per
// delivered alert (bounded 1:1 against visible pushes, not background chatter),
// and CLAUDE_DASH_RESOLVE_PUSH=0 turns it off. If a resolve is ever refused or
// dropped, app.js's foreground sweep still clears the stale banner on next open.
function resolveAlert(d) {
  return self.registration.getNotifications({ tag: d.tag || tagFor(d.sid) })
    .then((ns) => ns.forEach((n) => n.close()))
    .catch(() => {});
}

// A push arrives as the JSON the server encrypted (notify/channels.py):
// an ALERT {title, body, sid, url, badge} or a RETRACTION {type:"resolve", sid,
// tag, badge}. userVisibleOnly subscriptions MUST show a notification for every
// push, so a missing/blank payload still surfaces one — the resolve is the sole
// deliberate exception (see resolveAlert).
self.addEventListener("push", (e) => {
  let d = {};
  try { d = e.data ? e.data.json() : {}; } catch (_) { d = {}; }
  const title = d.title || "baqylau";
  const body = d.body || "";
  const sid = d.sid || "";
  // update the app-icon badge to the needs-you count carried in the push, so
  // the icon is right even though the app is closed (the app itself keeps the
  // badge live from the sessions snapshot while open). docs/dashboard.md
  // *Installed-app polish*. Ahead of the type split: a resolve carries the
  // decremented count, and fixing the icon is half of what it is for.
  if (typeof d.badge === "number" && "setAppBadge" in self.navigator) {
    (d.badge ? self.navigator.setAppBadge(d.badge) : self.navigator.clearAppBadge())
      .catch(() => {});
  }
  if (d.type === "resolve") { e.waitUntil(resolveAlert(d)); return; }
  e.waitUntil(self.registration.showNotification(title, {
    body,
    // tag collapses repeat alerts for the same session into one banner (a
    // re-fired asking/done replaces rather than stacks), and is what a resolve
    // push closes by.
    tag: tagFor(sid),
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
