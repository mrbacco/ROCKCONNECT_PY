/*
 * File: main.js
 * Author: mrbacco04@gmail.com
 * Date: 2026-10-02
 *
 * Site-wide script: keeps the red "unread messages" badge in the navbar up to date.
 */
(function () {
  "use strict";
  var badge = document.getElementById("unread-badge");
  if (!badge) { return; }               // not signed in: no badge on the page

  function refresh() {
    if (document.hidden) { return; }
    fetch(badge.getAttribute("data-url"),
          { credentials: "same-origin", headers: { "X-Requested-With": "fetch" } })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (data) {
        if (!data) { return; }
        badge.textContent = data.count;
        badge.style.display = data.count ? "inline-block" : "none";
      })
      .catch(function () { /* ignore: next tick retries */ });
  }
  setInterval(refresh, 10000);
})();
