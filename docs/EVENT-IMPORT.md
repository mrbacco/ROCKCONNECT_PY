<!--
  File: EVENT-IMPORT.md
  Author: mrbacco04@gmail.com
  Date: 2026-10-05
-->
# Gigs from outside services: any city in the world

A new community has no gigs yet, so "Gigs near you" would be empty. rockconnect fills it with upcoming concerts from event
services. Imported gigs show "via Ticketmaster" (or the other source) with a **Tickets** link; they are not posts (not in the
feed, no comments) and members cannot edit them. Gigs announced by members always come first and win when both list the same concert.

## How it works: on demand, anywhere

When someone searches "near me" in a place nobody searched before, the server fetches the ~28 km area around it from your
providers (a quick first batch, then the rest in the background) and remembers it for 12 hours. The next search there is instant.
So **every city in the world works as soon as a provider covers it**; you do not list cities anywhere.

* **Privacy:** only the *rounded grid square* (centre of a 0.25 degree cell, about 28 km) goes to the providers, from your server, with
  your key. Never the exact position, never who searched. This is stated in the privacy policy page.
* **Limits that protect your quota:** at most 60 new areas per hour for the whole site (`RATE_LIMIT_ONDEMAND_SITE`), one fetch even
  if many members search the same new city at once, failed areas are retried after 10 minutes.
* Stored events are deleted when over, when the provider stops listing them, or when not refreshed for 48 hours (providers only
  allow short-term storage). Switch the whole thing off with `IMPORT_ON_DEMAND=0` (then use the scheduled import below).

## What each provider covers

| Provider | Coverage | Key | Setting |
|---|---|---|---|
| **Ticketmaster** | US, Canada, Mexico, UK, Ireland, Germany, Spain, Netherlands, Nordics, Australia, NZ, South Africa and more. **Not France, Japan, India** (we saw 0 results in Paris, Tokyo, Mumbai) | free, instant: [developer.ticketmaster.com](https://developer.ticketmaster.com) | `TICKETMASTER_API_KEY` |
| **Skiddle** | UK and Ireland (strong for clubs and small gigs) | free, apply at skiddle.com/api/join.php | `SKIDDLE_API_KEY` |
| **Songkick** | Worldwide, including the places Ticketmaster misses | **by application only** (songkick.com/api_key_requests/new); they approve few new projects | `SONGKICK_API_KEY` |
| **PredictHQ** | Worldwide. Event data rather than a ticket shop: concerts with venue, date and place, but **no ticket links**. Only events with a named venue are used | paid, with a free trial: [predicthq.com](https://www.predicthq.com) | `PREDICTHQ_API_KEY` |
| **Bandsintown** | One artist at a time. **No search by place** | each artist's *own* app id | a band connects itself in its profile, see below |

Verified against the live service: Ticketmaster (London, New York, Toronto, Dublin, Berlin, Sydney, Mexico City, Sao Paulo, Nairobi
return real gigs). **Skiddle, Songkick and PredictHQ were written from their public documentation but not run against the live services**
(no keys available while building); their answer formats are read leniently, and `flask --app wsgi doctor --online` tells you
at once if a key works and whether events could be read. If you get a key, please run it and report what it says.

Where no provider has listings, an admin who searches there sees why in the panel, and members can still announce gigs themselves.

## Setup (five minutes)

1. Copy `.env.example` to `.env` (next to `run.py`) and add what you have:
   ```
   TICKETMASTER_API_KEY=your-key
   SKIDDLE_API_KEY=your-key          # optional, UK/Ireland
   SONGKICK_API_KEY=your-key         # optional, worldwide, when approved
   PREDICTHQ_API_KEY=your-token      # optional, worldwide, paid (free trial)
   ```
   `python run.py` and `flask --app wsgi ...` read `.env` by themselves. Restart after changing it.
2. `flask --app wsgi doctor --online` checks every key with one request each.
3. Done. Search for a gig in any city. Optional: keep favourite places always fresh from cron with
   `IMPORT_AREAS=London=51.5072,-0.1276,40;Dublin=53.3498,-6.2603,30` and `flask --app wsgi import-events`
   (`--provider skiddle`, `--area London`, `--dry-run` narrow it). The command exits with an error code when something failed.

## How to get each key (Songkick, PredictHQ, Bandsintown)
**Songkick** (site-wide key, worldwide)
1. Apply at [songkick.com/api_key_requests/new](https://www.songkick.com/api_key_requests/new): describe the site, say it is non-commercial or what the use is, and
   link it. They approve few new projects and answer slowly; there is no instant key.
2. When you get the key, put `SONGKICK_API_KEY=...` in `.env`, restart, run `flask --app wsgi doctor --online`.

**PredictHQ** (site-wide token, worldwide)
1. Create an account at [predicthq.com](https://www.predicthq.com) (there is a free trial), then create an **access token** in the
   account's API / developer section.
2. Put `PREDICTHQ_API_KEY=...` in `.env`, restart, run `flask --app wsgi doctor --online`. It is sent as a bearer token, never in the address.
3. Their free trial is for evaluation; a live commercial site needs a paid plan, so check their terms first. It lists
   concerts without ticket links, so members see the gig but not a "Get tickets" button; where Ticketmaster or Skiddle list the
   same concert, theirs wins and PredictHQ's copy is dropped.

**Bandsintown** (no site-wide key, on purpose)
1. Each **band** asks Bandsintown for its own app id (it is for the artist or someone acting for them; commercial use needs their
   written approval: see below).
2. The band opens *Edit profile*, enters its Bandsintown artist name and app id, and its dates appear as its gigs.
There is nothing for you to put in `.env`.

## Bands and Bandsintown

Bandsintown's terms say its API is for artists (or people acting for them), every app id belongs to one artist, and commercial use
needs their written approval. So the site never uses a key of its own. Instead a **band** can connect itself: in *Edit profile*
a band enters its Bandsintown artist name and **its own app id** (requested from Bandsintown). Its upcoming dates are then listed
as its gigs ("via Bandsintown", linked to its band profile) and refreshed by the daily import. The app id is never shown again,
logged or exported, and disconnecting deletes the dates.

## Read this before you sell or host it for customers
**Each provider's terms apply to you.** Ticketmaster's say, among other things, that you may not "cache or store any Event Content
other than for reasonable periods" (why data is short-lived here) and may not "derive revenues from the use or provision of the
Ticketmaster API". Bandsintown requires written approval for commercial use and unmodified branding. Songkick and Skiddle have
their own conditions (Songkick asks for attribution). **Ask each provider whether your use is allowed before you offer the feature
commercially.** The software is built so that every installation uses its **own operator's keys** and the feature stays off until a
key is set; community gigs work without it. This is not legal advice.
Terms: [Ticketmaster](https://developer.ticketmaster.com/support/terms-of-use/),
[Bandsintown](https://corp.bandsintown.com/data-applications-terms).

## Known limits
* One Ticketmaster search returns at most 1,000 events, so a busy city is read in several consecutive searches (London: about 2,000
  concerts in 4 months, 12 calls). Other providers are paged similarly.
* **Provider data errors:** a few venues have wrong coordinates in Ticketmaster's own data (a Manchester club pinned in central
  London, about 1 event in 100). The importer trusts the provider's position, so an **admin can hide such an event**: open it
  from the gig results and press *Hide this event*. It is removed and never imported again; *Admin -> Hidden events* lists them
  and brings one back.
* The same concert listed by two sources is shown once (same day, within about 300 m, mostly the same name). Two entries of one source
  are never merged.
* Ticketmaster's area parameter `latlong` is marked deprecated; if it is rejected (HTTP 400) the importer retries with `geoPoint`.
* **CONTACT_EMAIL must be a real address**: typed-town search uses OpenStreetMap, which answers HTTP 403 to placeholders like
  `hello@example.com`. `doctor` warns about it.
* Event times are the venue's local time, shown without a time zone.
* SQLite is fine for a small site; with many simultaneous new cities prefer PostgreSQL (the background import writes a lot).
