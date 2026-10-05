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

## How a member's gig gets its position
1. **The location button** in the gig form (best when posting from the venue): the browser sends exact coordinates.
2. **The place name**, if no coordinates were sent: it is looked up with the geocoder (`GEOCODER`, default
   OpenStreetMap Nominatim; set `GEOCODER=none` to turn it off, `GEOCODER_URL` to use your own server) and the answer
   is cached in the `geocache` table.
3. Neither works: the gig is posted without a position, the poster is told, and it just does not show in nearby searches.

## Browser notes
The browser's location feature only works on **https** pages (and on `localhost`). The site's `Permissions-Policy`
allows it for the site itself only.
