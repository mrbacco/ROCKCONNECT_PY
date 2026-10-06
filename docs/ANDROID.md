<!--
  File: ANDROID.md
  Author: mrbacco04@gmail.com
  Date: 2026-10-05
-->
# Preparing for the Android app

The plan is to offer rockconnect as an Android app. This is where the server stands and what is still to do. Nothing
below needs a rewrite: the web app stays, the app is a second client of the same server.

## Already done with the app in mind
* **A versioned JSON API** at `/api/v1` ([API.md](API.md)) that will not change in a breaking way for installed apps.
* **`GET /api/v1/meta`** (public): name, tagline, accent colour and feature flags, so one app build can carry a
  customer's branding and switch features on and off (white-label).
* **One uniform shape** for every gig whatever its source (members' or imported), so the app has one parser.
* **Privacy by design for location**: the app sends a position rounded to about 1 km, nothing is stored or logged.
* **The social side in the API**: going, who is going, people search with skill levels, follows, a notifications list (the
  bell), the gig discussion, and `.ics` calendar files ([API.md](API.md), [SOCIAL.md](SOCIAL.md)). The app must handle
  `403 age_required` (an older account that has not given a date of birth yet).
* **Plain HTTPS + JSON** and `Cache-Control: no-store` on location-dependent answers.

## To do before the app can ship
1. **Token sign-in for apps.** Today the API uses the website's cookie. Add `POST /api/v1/auth/token` (username +
   password -> access token + refresh token), `POST /api/v1/auth/refresh` and `POST /api/v1/auth/revoke`. Store only
   token hashes (like `sessions` today), keep the 30-day sliding lifetime, and let "sign out on all devices" revoke
   them too. The app keeps the tokens in the Android Keystore, never in plain preferences.
2. **The rest of the API**: feed (list, post with photo upload, like, comment, delete), profile and edit, people
   search, chat (list, send, poll or push), report and block, account (export, delete). The web views already hold the
   logic; each needs a JSON twin under `/api/v1`.
3. **Push notifications** (Firebase Cloud Messaging) instead of polling: new chat message, a gig announced near me, and the
   `notifications` the site already creates (new follower, "a friend is going"): send a push whenever `notifications.notify` writes one.
   Needs a `devices` table (token, platform, member) and a sender.
4. **Deep links**: `/posts/<id>` and `/events/<id>` already exist as stable addresses to open in the app.
5. **Google Play requirements**: a privacy policy URL (the site already has one, `/privacy`), the Data Safety form
   (location is used for the search only and not stored, say so), account deletion inside the app and on the web
   (both exist), and report/block for user-generated content (exists).
6. **Stores and the API terms**: imported listings keep linking to Ticketmaster's ticket page (the web does, so should the app); read
   [EVENT-IMPORT.md](EVENT-IMPORT.md) about their terms before charging for the app.

## Suggested order
token sign-in -> feed/profile/people API -> chat API -> push -> release candidate. The nearby search already works end to
end and is a good first screen for a prototype.
