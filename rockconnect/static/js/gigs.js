/*
 * File: gigs.js
 * Author: mrbacco04@gmail.com
 * Date: 2026-10-05
 *
 * "Gigs near you" on the gig board (loaded only on /gigs):
 *  - "Use my current location" asks the browser for the position and calls GET /api/v1/gigs/nearby
 *  - or type a town / use the town of your profile: the server finds it on the map
 *  - results are gigs announced by members plus listings imported from the event providers (marked, with a tickets link)
 *  - every result has Going / Interested buttons and the number of members going
 * The position is rounded to 2 decimals (about 1 km) before it is sent: plenty for "near me", and it keeps
 * the exact spot out of any web server log. Results are built with textContent (never innerHTML).
 */
(function () {
  "use strict";
  var panel = document.getElementById("nearby");
  if (!panel) { return; }

  var statusEl = document.getElementById("nearby-status");
  var list = document.getElementById("nearby-results");
  var radius = document.getElementById("nearby-radius");
  var apiUrl = panel.getAttribute("data-url");

  function say(text) { statusEl.textContent = text; }

  function element(tag, className, text) {
    var el = document.createElement(tag);
    if (className) { el.className = className; }
    if (text !== undefined) { el.textContent = text; }
    return el;
  }

  function render(data) {
    list.textContent = "";
    if (!data.gigs.length) {
      say("No gigs found within " + data.radius_km + " km of that spot. Try a bigger distance: new gigs appear as soon as "
          + "bands and venues announce them." + (data.hint ? "  " + data.hint : ""));
      return;
    }
    say(data.total + (data.total === 1 ? " gig" : " gigs") + " within " + data.radius_km + " km"
        + (data.total > data.count ? " (showing the nearest " + data.count + ")" : "") + ", nearest first.");
    data.gigs.forEach(function (gig) {
      var li = element("li", "list-group-item px-0");
      var top = element("div", "d-flex justify-content-between align-items-baseline");
      var link = element("a", "author", gig.title);
      link.href = gig.url;
      top.appendChild(link);
      top.appendChild(element("span", "text-amber font-weight-bold", gig.distance_km + " km"));
      li.appendChild(top);
      li.appendChild(element("div", "gig-when small", gig.event_label + (gig.place ? "  ·  " + gig.place : "")));
      if (gig.body) {
        li.appendChild(element("div", "small text-muted", gig.body.length > 140 ? gig.body.slice(0, 140) + "…" : gig.body));
      }
      if (gig.source !== "community") {            // an imported listing: say where it is from, link to the tickets
        var from = element("div", "small mt-1");
        from.appendChild(element("span", "kind-badge ml-0 mr-2", "via " + (gig.attribution || gig.source)));
        if (gig.ticket_url) {
          var tickets = element("a", "", "Tickets");
          tickets.href = gig.ticket_url;
          tickets.target = "_blank";
          tickets.rel = "noopener nofollow sponsored";
          from.appendChild(tickets);
        }
        li.appendChild(from);
      }
      li.appendChild(attendanceButtons(gig));
      list.appendChild(li);
    });
  }

  // "Going" and "Interested" under each result; the answer of the server updates the counts in place
  var attendUrl = panel.getAttribute("data-attend-url");
  var csrf = panel.getAttribute("data-csrf");

  function attendanceButtons(gig) {
    var box = element("div", "gig-actions d-flex flex-wrap align-items-center mt-1");
    var going = element("button", "", "✓ Going");
    var interested = element("button", "", "★ Interested");
    var counts = element("span", "small text-muted ml-1");
    going.type = interested.type = "button";

    function paint() {
      going.className = "btn btn-sm mr-1 " + (gig.my_status === "going" ? "btn-primary" : "btn-outline-primary");
      interested.className = "btn btn-sm mr-2 " + (gig.my_status === "interested" ? "btn-primary" : "btn-outline-dark");
      counts.textContent = gig.going_count + " going · " + gig.interested_count + " interested"
        + (gig.friends_going ? " · " + gig.friends_going + " you follow" : "");
    }

    function send(status) {
      var form = new FormData();
      form.append("_csrf", csrf);
      form.append("source", gig.source);
      form.append("ref", gig.ref);
      form.append("status", status);
      fetch(attendUrl, { method: "POST", body: form, credentials: "same-origin", headers: { "X-Requested-With": "fetch" } })
        .then(function (r) {
          return r.json().catch(function () { return {}; }).then(function (body) { return { status: r.status, ok: r.ok, body: body }; });
        })
        .then(function (res) {
          if (res.status === 401) { window.rcSessionExpired(res.body.login_url); return; }
          if (!res.ok) { say(res.body.error || "Could not save that, please try again."); return; }
          gig.my_status = res.body.status;
          gig.going_count = res.body.going_count;
          gig.interested_count = res.body.interested_count;
          paint();
        })
        .catch(function () { say("Could not reach the server. Check your connection and try again."); });
    }

    going.addEventListener("click", function () { send(gig.my_status === "going" ? "none" : "going"); });
    interested.addEventListener("click", function () { send(gig.my_status === "interested" ? "none" : "interested"); });
    paint();
    box.appendChild(going);
    box.appendChild(interested);
    box.appendChild(counts);
    return box;
  }

  var latest = 0;   // the number of the newest search: an older answer arriving late must not overwrite it

  function search(params) {
    var mine = ++latest;
    list.textContent = "";
    say("Looking for gigs (a new place can take a few seconds)...");
    params.radius_km = radius.value;
    var query = Object.keys(params).map(function (k) { return k + "=" + encodeURIComponent(params[k]); }).join("&");
    fetch(apiUrl + "?" + query, { credentials: "same-origin", headers: { "X-Requested-With": "fetch" } })
      .then(function (r) {
        return r.json().catch(function () { return {}; }).then(function (body) { return { status: r.status, body: body }; });
      })
      .then(function (res) {
        if (mine !== latest) { return; }          // a newer search was started meanwhile: this answer is stale
        if (res.status === 401) { window.rcSessionExpired(res.body.login_url); return; }
        if (res.status === 429) { say("Slow down a little and try again in a minute."); return; }
        if (res.status >= 400) { say(res.body.error || "Something went wrong, please try again."); return; }
        render(res.body);
      })
      .catch(function () {
        if (mine === latest) { say("Could not reach the server. Check your connection and try again."); }
      });
  }

  document.getElementById("nearby-locate").addEventListener("click", function () {
    if (!navigator.geolocation) {
      say("Your browser cannot share its location. Type a town below instead.");
      return;
    }
    say("Waiting for your browser to share your location...");
    navigator.geolocation.getCurrentPosition(function (pos) {
      search({ lat: pos.coords.latitude.toFixed(2), lon: pos.coords.longitude.toFixed(2) });
    }, function (err) {
      say(err.code === 1
        ? "Location sharing is blocked for this site. Allow it in your browser, or type a town below."
        : "Could not work out where you are. Type a town below instead.");
    }, { enableHighAccuracy: false, timeout: 10000, maximumAge: 300000 });
  });

  document.getElementById("nearby-form").addEventListener("submit", function (e) {
    e.preventDefault();
    var place = document.getElementById("nearby-place").value.trim();
    if (place) { search({ q: place }); } else { say("Type a town first."); }
  });

  var profileBtn = document.getElementById("nearby-profile");
  if (profileBtn) {
    profileBtn.addEventListener("click", function () { search({ profile: "1" }); });
  }
})();
