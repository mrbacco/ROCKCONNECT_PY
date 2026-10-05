/*
 * File: main.js
 * Author: mrbacco04@gmail.com
 * Date: 2026-10-05
 *
 * Site-wide script for signed-in pages:
 *  1. the red "unread messages" badge, refreshed every 10 s
 *  2. the "use my current location" button of the gig form
 *  3. forms with data-confirm="..." ask before submitting; the photo picker shows the chosen file name
 * There are no inline scripts or styles anywhere: the Content-Security-Policy forbids them.
 * If the server answers a background request with 401 (signed out elsewhere, or away for very long)
 * we go to the sign-in page.
 */
(function () {
  "use strict";

  function goToSignIn(loginUrl) {
    var back = window.location.pathname + window.location.search;
    window.location.href = loginUrl + "?next=" + encodeURIComponent(back);
  }
  // shared with chat.js
  window.rcSessionExpired = goToSignIn;

  // ---- 1. unread badge -----------------------------------------------------
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
          badge.classList.toggle("d-none", !data.count);
        })
        .catch(function () { /* ignore: next tick retries */ });
    };
    setInterval(refresh, 10000);
  }

  // ---- 3. confirm dialogs and photo picker ----------------------------------
  document.addEventListener("submit", function (e) {
    var message = e.target.getAttribute && e.target.getAttribute("data-confirm");
    if (message && !window.confirm(message)) { e.preventDefault(); }
  });
  // ---- 2. "use my current location" in the gig form -------------------------
  // Fills two hidden fields with this device's position (about 10 m precision) so the gig can be found by
  // "gigs near me". Without it the server looks the typed place name up on the map instead.
  Array.prototype.forEach.call(document.querySelectorAll("[data-use-location]"), function (btn) {
    var box = btn.parentNode;
    var lat = box.querySelector("[data-location-lat]");
    var lon = box.querySelector("[data-location-lon]");
    var status = box.querySelector("[data-location-status]");
    btn.addEventListener("click", function () {
      if (!navigator.geolocation) { status.textContent = "Not supported by this browser."; return; }
      status.textContent = "Locating...";
      navigator.geolocation.getCurrentPosition(function (pos) {
        lat.value = pos.coords.latitude.toFixed(4);
        lon.value = pos.coords.longitude.toFixed(4);
        status.textContent = "✔ Position added to this gig";
      }, function () {
        lat.value = ""; lon.value = "";
        status.textContent = "Could not get your location, the place name will be used instead.";
      }, { enableHighAccuracy: true, timeout: 10000, maximumAge: 60000 });
    });
  });
  Array.prototype.forEach.call(document.querySelectorAll("[data-photo-input]"), function (input) {
    input.addEventListener("change", function () {
      var label = input.previousElementSibling;
      label.textContent = input.files.length ? "✔ " + input.files[0].name : "📷 Photo";
    });
  });
})();
