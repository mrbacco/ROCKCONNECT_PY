/*
 * File: chat.js
 * Author: mrbacco04@gmail.com
 * Date: 2026-10-02
 *
 * Live chat for one conversation (loaded only on /conversations/<id>):
 *  - polls the server every 2 s for new messages (no page reload)
 *  - sends messages with fetch() and shows them straight away
 *  - Enter sends, Shift+Enter adds a new line
 * Message text is always inserted with textContent (never innerHTML), so it cannot inject HTML.
 */
(function () {
  "use strict";
  var chat = document.getElementById("chat");
  if (!chat) { return; }

  var box = document.getElementById("messages");
  var form = document.getElementById("chat-form");
  var input = document.getElementById("chat-input");
  var sendBtn = document.getElementById("chat-send");
  var errorEl = document.getElementById("chat-error");
  var lastId = parseInt(chat.getAttribute("data-last-id"), 10) || 0;
  var seen = {};                       // ids already on screen, so a message is never shown twice
  var POLL_MS = 2000;

  // "2026-10-02 23:15:01" is UTC; show it in the viewer's own time zone
  function localTime(utc) {
    var d = new Date(utc.replace(" ", "T") + "Z");
    if (isNaN(d.getTime())) { return utc + " UTC"; }
    return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  }

  Array.prototype.forEach.call(box.querySelectorAll(".message"), function (el) {
    seen[el.getAttribute("data-id")] = true;
    var meta = el.querySelector(".meta");
    if (meta) { meta.textContent = localTime(meta.getAttribute("data-utc")); }
  });

  function nearBottom() {
    return box.scrollHeight - box.scrollTop - box.clientHeight < 80;
  }
  function scrollDown() { box.scrollTop = box.scrollHeight; }

  function addMessage(m) {
    if (seen[m.id]) { return; }
    seen[m.id] = true;
    if (m.id > lastId) { lastId = m.id; }
    var empty = document.getElementById("empty");
    if (empty) { empty.remove(); }

    var wrap = document.createElement("div");
    wrap.className = "message " + (m.mine ? "mine" : "theirs");
    wrap.setAttribute("data-id", m.id);
    var body = document.createElement("div");
    body.className = "body";
    body.textContent = m.body;
    var meta = document.createElement("div");
    meta.className = "meta";
    meta.setAttribute("data-utc", m.created_at);
    meta.textContent = localTime(m.created_at);
    wrap.appendChild(body);
    wrap.appendChild(meta);
    box.appendChild(wrap);
  }

  function poll() {
    if (document.hidden) { return; }   // do not hammer the server from a background tab
    fetch(chat.getAttribute("data-poll-url") + "?after=" + lastId,
          { credentials: "same-origin", headers: { "X-Requested-With": "fetch" } })
      .then(function (r) {
        if (r.status === 401) {          // session expired: back to the sign-in page
          return r.json().then(function (d) { window.rcSessionExpired(d.login_url); return null; });
        }
        return r.ok ? r.json() : null;
      })
      .then(function (data) {
        if (!data || !data.messages.length) { return; }
        var stick = nearBottom();      // only auto-scroll if the reader is already at the bottom
        data.messages.forEach(addMessage);
        if (stick) { scrollDown(); }
      })
      .catch(function () { /* network blip: the next poll retries */ });
  }

  form.addEventListener("submit", function (e) {
    e.preventDefault();
    if (!input.value.trim()) { return; }
    sendBtn.disabled = true;
    errorEl.textContent = "";
    fetch(form.action, {
      method: "POST",
      body: new FormData(form),        // includes the CSRF token
      credentials: "same-origin",
      headers: { "X-Requested-With": "fetch" }
    })
      .then(function (r) {
        return r.json().catch(function () { return {}; }).then(function (d) { return { ok: r.ok, status: r.status, data: d }; });
      })
      .then(function (res) {
        if (res.status === 401) {        // session expired while typing: explain, then go to sign in
          errorEl.textContent = "Your session has expired. Taking you to the sign-in page...";
          setTimeout(function () { window.rcSessionExpired(res.data.login_url); }, 1500);
          return;
        }
        if (!res.ok) { throw new Error(res.data.error || "Could not send, please try again."); }
        addMessage(res.data.message);
        input.value = "";
        scrollDown();
      })
      .catch(function (err) { errorEl.textContent = err.message; })
      .then(function () { sendBtn.disabled = false; input.focus(); });
  });

  input.addEventListener("keydown", function (e) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      form.requestSubmit();
    }
  });

  scrollDown();
  setInterval(poll, POLL_MS);
})();
