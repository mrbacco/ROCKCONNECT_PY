/*
 * File: main.js
 * Author: mrbacco04@gmail.com
 * Date: 2026-10-02
 *
 * Site-wide script for signed-in pages:
 *  1. session countdown in the menu bar; at 0 the browser goes to the sign-in page
 *  2. the red "unread messages" badge, refreshed every 10 s
 * If the server answers a background request with 401 (session expired) we go to the sign-in page too.
 */
(function () {
  "use strict";

  function goToSignIn(loginUrl) {
    var back = window.location.pathname + window.location.search;
    window.location.href = loginUrl + "?next=" + encodeURIComponent(back);
  }
  // shared with chat.js
  window.rcSessionExpired = goToSignIn;

  // ---- 1. session countdown ------------------------------------------------
  var timer = document.getElementById("session-timer");
  if (timer) {
    var secondsLeft = parseInt(timer.getAttribute("data-seconds-left"), 10) || 0;
    var loginUrl = timer.getAttribute("data-login-url");
    var label = timer.querySelector("b");
    var loadedAt = Date.now();   // count from the moment the page arrived (client clock only for differences)

    var tick = function () {
      var remaining = secondsLeft - Math.floor((Date.now() - loadedAt) / 1000);
      if (remaining <= -1) {     // one second of grace so the server has surely expired it too
        label.textContent = "expired";
        goToSignIn(loginUrl);
        return;
      }
      remaining = Math.max(0, remaining);
      var m = Math.floor(remaining / 60), s = remaining % 60;
      label.textContent = m >= 10 ? m + " min" : m + ":" + (s < 10 ? "0" : "") + s;
      timer.classList.toggle("ending", remaining < 300);   // last 5 minutes: highlighted
    };
    tick();
    setInterval(tick, 1000);
  }

  // ---- 2. unread badge -----------------------------------------------------
  var badge = document.getElementById("unread-badge");
  if (badge) {
    var refresh = function () {
      if (document.hidden) { return; }
      fetch(badge.getAttribute("data-url"),
            { credentials: "same-origin", headers: { "X-Requested-With": "fetch" } })
        .then(function (r) {
          if (r.status === 401) {
            return r.json().then(function (d) { goToSignIn(d.login_url); return null; });
          }
          return r.ok ? r.json() : null;
        })
        .then(function (data) {
          if (!data) { return; }
          badge.textContent = data.count;
          badge.style.display = data.count ? "inline-block" : "none";
        })
        .catch(function () { /* ignore: next tick retries */ });
    };
    setInterval(refresh, 10000);
  }
})();
