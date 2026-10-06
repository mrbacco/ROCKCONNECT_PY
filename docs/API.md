<!--
  File: API.md
  Author: mrbacco04@gmail.com
  Date: 2026-10-05
-->
# JSON API, version 1

The API is under **`/api/v1`**. The version is in the URL on purpose: an installed mobile app cannot be force-updated, so
what `/api/v1` answers must not change in a way that breaks it. New fields may appear at any time (clients must ignore
fields they do not know); a change or removal means a new `/api/v2`.

Today it uses the same sign-in as the website (the `session` cookie). For the planned Android app see
[ANDROID.md](ANDROID.md). All answers are JSON. A missing or ended sign-in gives `401`
`{"error": "session_expired", "code": "session_expired", "login_url": "..."}`; errors look like
`{"error": "text for humans", "code": "machine_code"}`, including `429` when a rate limit is hit.

## GET /api/v1/meta (public)

What an app needs before anyone signs in: the API version, the site's branding and which features this server has.
```json
{"api_version": 1, "server_version": "1.0.0",
 "site": {"name": "rockconnect", "tagline": "...", "accent_color": "#ffb000", "contact": "hello@example.com"},
 "features": {"nearby_gigs": true, "imported_events": true, "event_sources": ["Ticketmaster", "Skiddle"], "place_search": true}}
```

## GET /api/v1/gigs/nearby

Upcoming gigs close to a position, nearest first: gigs announced by members **and** gigs imported from the event providers
(Ticketmaster, Skiddle, Songkick, and bands' own Bandsintown pages: see [EVENT-IMPORT.md](EVENT-IMPORT.md)). Works for any place in the
world: the first search around a new place fetches it from the providers (a second or two), later searches are instant.

| Parameter | Meaning |
|---|---|
| `lat`, `lon` | the position to search around, decimal degrees. Round them to 2 decimals (about 1 km): that is plenty and keeps exact positions out of server logs |
| `q` | instead of lat/lon: a place name, e.g. `q=Galway`. The server finds it on the map |
| `profile=1` | instead of both: use the location written in the member's profile |
| `radius_km` | default 25, between 1 and 500 |
| `days` | how far ahead to look, default 90, up to 365 |
| `limit` | default 50, up to 100 |
| `sort` | `distance` (default) or `date` |
| `source` | `all` (default), `community` (members' gigs) or `external` (imported listings) |

```
GET /api/v1/gigs/nearby?lat=51.51&lon=-0.13&radius_km=30
```
```json
{
  "center": {"latitude": 51.51, "longitude": -0.13},
  "radius_km": 30.0, "sort": "distance", "source": "all", "total": 2, "count": 2,
  "gigs": [
    {"source": "ticketmaster", "id": 41, "title": "The Maple Kings", "url": "/events/41",
     "ticket_url": "https://www.ticketmaster.co.uk/event/...", "body": "Rock",
     "event_at": "2026-10-09 20:00", "event_label": "Fri 09 Oct 2026, 20:00",
     "place": "O2 Academy Brixton, London", "latitude": 51.5033, "longitude": -0.1195,
     "distance_km": 1.0, "has_photo": false, "genre": "Rock", "attribution": "Ticketmaster", "author": null},
    {"source": "community", "id": 12, "title": "The Hollow Kings", "url": "/posts/12", "ticket_url": null,
     "body": "Doors at 8, we hit the stage at 9.", "event_at": "2026-10-11 20:00",
     "event_label": "Sun 11 Oct 2026, 20:00", "place": "Camden Underworld", "latitude": 51.5391, "longitude": -0.1426,
     "distance_km": 3.6, "has_photo": true, "genre": null, "attribution": null,
     "author": {"id": 3, "username": "the_hollow_kings", "name": "The Hollow Kings", "kind": "band"}}
  ]
}
```
Every item has **exactly the same keys**, whatever its `source` (`community`, `ticketmaster`, `skiddle`, `songkick`, `bandsintown`);
`(source, id)` identifies it. The same concert listed by several sources appears once. `author` is set for members' gigs and for
a band's own Bandsintown dates, `null` otherwise. `hint` is only filled for admins, when nothing was found, to say why. `event_at` is the local
time at the venue, without a time zone. For imported items show `attribution` ("via Ticketmaster") and offer
`ticket_url` as the way to buy; for member gigs `url` is the post. Gigs of suspended members, and of members who blocked
you or whom you blocked, are never returned. The answer is `Cache-Control: no-store`.

Errors: `location_required`, `bad_coordinates`, `bad_radius_km`, `bad_days`, `bad_limit`, `bad_sort`, `bad_source`,
`no_profile_location` (400), `place_not_found` (404), rate limit `429` (60 searches per 10 minutes, and 20 place-name
lookups per hour, per member).

## Going to gigs and finding people

A gig is identified by `(source, ref)`: `source` is `community` (ref = the post id) or a provider (`ticketmaster`, `skiddle`, ...
with the provider's own event id). Every item of `/gigs/nearby` carries `source`, `ref`, `genre_key`, `going_count`,
`interested_count` and `my_status`. The same concert listed by two sources counts as one: people who said they are going on
different listings see each other.

| Endpoint | What it does |
|---|---|
| `POST /api/v1/gigs/<source>/<ref>/attendance` | form fields `status` = `going`, `interested` or `none`; and/or `visible` = `0` / `1` (private = counted but not named). Answer: `status`, `visible`, `going_count`, `interested_count` |
| `GET /api/v1/gigs/<source>/<ref>/attendees` | who else is going, by name. Filters: `instrument`, `genre`, `goal` (keys from `/lists`), `rsvp` = `going` / `interested` |
| `GET /api/v1/people` | search members: `q`, `kind`, `instrument`, `genre`, `goal`, `limit` (max 100), `offset` |
| `GET /api/v1/me/gigs` | my upcoming plans |
| `GET /api/v1/lists` (public) | the fixed lists of instruments, genres and goals, with labels. Never rename a key; keys may be added |
| `GET /api/v1/gigs/nearby?genre=jazz` | the nearby search also filters by genre |

A person in these answers has `id`, `username`, `name`, `kind`, `location`, `instruments`, `genres`, `goals` (and `status` in
attendee lists); never an e-mail address. Members who are suspended, who blocked you or whom you blocked, who hid all their plans,
or whose RSVP is private are not listed (private RSVPs are still counted). Errors: `bad_status`, `gig_not_found` (404),
`gig_over`, `bad_instrument`, `bad_genre`, `bad_goal`, `missing_status`; `429` when too many requests.

### Follows, notifications, gig discussion

| Endpoint | What it does |
|---|---|
| `POST /api/v1/people/<id>/follow`, `.../unfollow` | answer `following` (true/false) and `followers` (count) |
| `GET /api/v1/me/following`, `GET /api/v1/me/followers` | the people, in the same shape as `/people` |
| `GET /api/v1/notifications` | `unread`, `count`, `notifications`: `id`, `kind` (`follow`, `going`), `text`, `url`, `created_at`, `read` |
| `POST /api/v1/notifications/read` | mark all as read; answer `unread` = 0 |
| `GET /api/v1/gigs/<source>/<ref>/comments` | the discussion: `total`, `comments` (`id`, `body`, `created_at`, `mine`, `author`) oldest first, last 100 |
| `POST /api/v1/gigs/<source>/<ref>/comments` | form field `body` (max 1000 characters); `201` with `id` |
| `POST /api/v1/gig-comments/<id>/delete` | the author or an admin; `403 forbidden` for others |

`/people`, `/gigs/.../attendees` and `/me/following` also take `level` (`beginner`, `intermediate`, `advanced`, `pro`, meaning
"at least", together with `instrument`), `/people` takes `relation` = `following` or `followers`, and persons carry
`instrument_levels` (instrument -> level or null) and `following`. Attendee lists put people you follow first, and the counts carry
`friends_going`. Calendar files are plain web downloads (`GET /gigs/<source>/<ref>.ics`, `GET /gigs/mine.ics`), not JSON.
`GET /api/v1/lists` now also returns `levels`. Errors: `bad_level`, `empty`, `too_long`, `closed` (the gig is long over), `email_not_confirmed`,
`not_found`, `forbidden`.

Every API call answers `403 age_required` for a member who has not given a date of birth yet (an older account): send them to
the website's *confirm your date of birth* page, or add that screen to the app. `/meta` lists the feature flags `follows`,
`notifications`, `skill_levels`, `gig_comments`, `calendar` and `age_check`.

State-changing calls are `POST` with the CSRF token (`_csrf`) while the API still uses the website cookie; the planned token sign-in
for the Android app replaces that (see ANDROID.md).

## How a member's gig gets its position
1. **The location button** in the gig form (best when posting from the venue): the browser sends exact coordinates.
2. **The place name**, if no coordinates were sent: it is looked up with the geocoder (`GEOCODER`, default
   OpenStreetMap Nominatim; set `GEOCODER=none` to turn it off, `GEOCODER_URL` to use your own server) and the answer
   is cached in the `geocache` table.
3. Neither works: the gig is posted without a position, the poster is told, and it just does not show in nearby searches.

## Browser notes
The browser's location feature only works on **https** pages (and on `localhost`). The site's `Permissions-Policy`
allows it for the site itself only.
