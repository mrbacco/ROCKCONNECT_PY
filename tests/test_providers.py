# File: test_providers.py
# Author: mrbacco04@gmail.com
# Date: 2026-10-05
"""Skiddle, Songkick and Bandsintown: reading their answers and building their requests."""
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlparse

import pytest

from rockconnect import importer, providers
from rockconnect.importer import Area, ImportFailed

LONDON = Area("London", 51.5072, -0.1276, 40)


def day(offset=3):
    return (datetime.now(timezone.utc) + timedelta(days=offset)).strftime("%Y-%m-%d")


def params(url):
    return {k: v[0] for k, v in parse_qs(urlparse(url).query).items()}


class Get:
    """A stand-in for the HTTP function: queued answers, remembers (url, provider, timeout)."""

    def __init__(self, *answers):
        self.answers, self.calls = list(answers), []

    def __call__(self, url, provider=None, timeout=None):
        self.calls.append((url, provider, timeout))
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer


# ------------------------------------------------------------------ Skiddle
def skiddle_event(n=1, **over):
    event = {"id": str(1000 + n), "eventname": "Skiddle Band %d" % n, "startdate": day() + " 20:00:00",
             "link": "https://www.skiddle.com/whats-on/London/x/%d/" % n, "eventcode": "LIVE",
             "venue": {"name": "Electric Ballroom", "town": "London", "latitude": "51.5391", "longitude": "-0.1426"},
             "genres": [{"genreid": "3", "name": "Indie"}]}
    event.update(over)
    return event


def test_skiddle_event_is_read():
    item = providers.normalise_skiddle(skiddle_event())
    assert item["source"] == "skiddle" and item["external_id"] == "1001" and item["title"] == "Skiddle Band 1"
    assert item["venue"] == "Electric Ballroom" and item["city"] == "London" and item["genre"] == "Indie"
    assert item["event_at"] == day() + " 20:00" and item["time_known"] == 1
    assert (item["latitude"], item["longitude"]) == (51.5391, -0.1426)
    assert item["ticket_url"].startswith("https://www.skiddle.com/")


def test_skiddle_time_comes_from_the_date_or_the_door_time_or_is_unknown():
    assert providers.normalise_skiddle(skiddle_event(startdate=day()))["time_known"] == 0
    assert providers.normalise_skiddle(skiddle_event(startdate=day()))["event_at"] == day() + " 00:00"
    doors = providers.normalise_skiddle(skiddle_event(startdate=day(), openingtimes={"doorsopen": "19:30"}))
    assert doors["event_at"] == day() + " 19:30" and doors["time_known"] == 1
    assert providers.normalise_skiddle(skiddle_event(startdate=day() + " 9:05"))["event_at"] == day() + " 09:05"


@pytest.mark.parametrize("change", [
    {"id": None}, {"eventname": ""}, {"startdate": "soon", "date": None}, {"venue": None}, {"venue": {}},
    {"venue": {"latitude": "0", "longitude": "0"}}, {"venue": {"latitude": "x", "longitude": "1"}},
    {"venue": {"latitude": "99", "longitude": "1"}}, {"startdate": None, "date": None},
])
def test_skiddle_unusable_events_are_skipped(change):
    event = skiddle_event()
    event.update(change)
    if change.get("id", 1) is None:
        event.pop("id")
    assert providers.normalise_skiddle(event) is None


def test_skiddle_links_and_genres_are_handled_safely():
    assert providers.normalise_skiddle(skiddle_event(link="javascript:alert(1)"))["ticket_url"] is None
    assert providers.normalise_skiddle(skiddle_event(link="http://insecure.example/x"))["ticket_url"] is None
    assert providers.normalise_skiddle(skiddle_event(genres=["Rock", "Pop"]))["genre"] == "Rock"
    assert providers.normalise_skiddle(skiddle_event(genres=None))["genre"] is None
    assert providers.normalise_skiddle(skiddle_event(genres="not a list"))["genre"] is None


def test_skiddle_request_is_correct():
    get = Get({"error": 0, "results": [], "totalcount": 0})
    list(providers.fetch_skiddle(LONDON, "KEY123", 30, get))
    url, provider, _ = get.calls[0]
    p = params(url)
    assert url.startswith("https://www.skiddle.com/api/v1/events/search/?") and provider == "Skiddle"
    assert p["api_key"] == "KEY123" and p["eventcode"] == "LIVE" and p["limit"] == "100" and p["offset"] == "0"
    assert p["latitude"] == "51.5072" and p["longitude"] == "-0.1276"
    assert p["radius"] == "25"                                 # 40 km = 24.9 miles, rounded up: Skiddle uses miles
    assert p["minDate"] == day(0) and p["maxDate"] == day(30)


def test_skiddle_pages_until_everything_is_read(monkeypatch):
    monkeypatch.setattr(providers.time, "sleep", lambda s: None)
    first = {"error": 0, "totalcount": 230, "results": [skiddle_event(i) for i in range(100)]}
    second = {"error": 0, "totalcount": 230, "results": [skiddle_event(i) for i in range(100, 200)]}
    third = {"error": 0, "totalcount": 230, "results": [skiddle_event(i) for i in range(200, 230)]}
    get = Get(first, second, third)
    items = list(providers.fetch_skiddle(LONDON, "K", 30, get))
    assert len(items) == 230 and [params(c[0])["offset"] for c in get.calls] == ["0", "100", "200"]


def test_skiddle_stops_early_and_says_so_when_told_to(monkeypatch):
    monkeypatch.setattr(providers.time, "sleep", lambda s: None)
    pages = [{"error": 0, "totalcount": 900, "results": [skiddle_event(i + p * 100) for i in range(100)]} for p in range(2)]
    stats = {}
    items = list(providers.fetch_skiddle(LONDON, "K", 30, Get(*pages), max_pages=2, stats=stats))
    assert len(items) == 200 and stats["truncated"] is True


def test_skiddle_error_answer_and_timeout_passthrough():
    with pytest.raises(ImportFailed, match="Skiddle answered with an error: bad key"):
        list(providers.fetch_skiddle(LONDON, "K", 30, Get({"error": 1, "errormessage": "bad key"})))
    get = Get({"error": 0, "results": []})
    list(providers.fetch_skiddle(LONDON, "K", 30, get, timeout=8))
    assert get.calls[0][2] == 8


# ------------------------------------------------------------------ Songkick
def songkick_event(n=1, **over):
    event = {"id": 5000 + n, "displayName": "Songkick Band %d at Venue (%s)" % (n, day()), "type": "Concert",
             "uri": "https://www.songkick.com/concerts/%d-x" % n,
             "start": {"date": day(), "time": "19:30:00", "datetime": day() + "T19:30:00+0100"},
             "location": {"city": "Paris, France", "lat": 48.85, "lng": 2.35},
             "venue": {"id": 1, "displayName": "La Cigale", "lat": 48.8823, "lng": 2.3399}}
    event.update(over)
    return event


def test_songkick_event_is_read():
    item = providers.normalise_songkick(songkick_event())
    assert item["source"] == "songkick" and item["external_id"] == "5001"
    assert item["venue"] == "La Cigale" and item["city"] == "Paris, France"
    assert item["event_at"] == day() + " 19:30" and (item["latitude"], item["longitude"]) == (48.8823, 2.3399)
    assert item["ticket_url"].startswith("https://www.songkick.com/")


def test_songkick_uses_the_venue_position_never_the_city_centre():
    no_venue_position = songkick_event(venue={"displayName": "Somewhere", "lat": None, "lng": None})
    assert providers.normalise_songkick(no_venue_position) is None       # the city centre would be the wrong street
    assert providers.normalise_songkick(songkick_event(start={"date": day(), "time": None}))["time_known"] == 0
    assert providers.normalise_songkick(songkick_event(start={"date": "never"})) is None
    assert providers.normalise_songkick(songkick_event(displayName="")) is None


def test_songkick_request_and_paging(monkeypatch):
    monkeypatch.setattr(providers.time, "sleep", lambda s: None)
    page1 = {"resultsPage": {"status": "ok", "totalEntries": 75, "perPage": 50, "page": 1,
                             "results": {"event": [songkick_event(i) for i in range(50)]}}}
    page2 = {"resultsPage": {"status": "ok", "totalEntries": 75, "perPage": 50, "page": 2,
                             "results": {"event": [songkick_event(i) for i in range(50, 75)]}}}
    get = Get(page1, page2)
    assert len(list(providers.fetch_songkick(Area("Paris", 48.8566, 2.3522, 30), "SK", 20, get))) == 75
    p = params(get.calls[0][0])
    assert get.calls[0][0].startswith("https://api.songkick.com/api/3.0/events.json?") and p["apikey"] == "SK"
    assert p["location"] == "geo:48.8566,2.3522" and p["per_page"] == "50" and p["page"] == "1"
    assert p["min_date"] == day(0) and p["max_date"] == day(20)          # Songkick needs both dates
    assert params(get.calls[1][0])["page"] == "2"


def test_songkick_empty_and_error_answers():
    assert list(providers.fetch_songkick(LONDON, "K", 20, Get({"resultsPage": {"status": "ok", "totalEntries": 0,
                                                                              "results": {}}}))) == []
    with pytest.raises(ImportFailed, match="Songkick answered with an error: Invalid API key"):
        list(providers.fetch_songkick(LONDON, "K", 20, Get({"resultsPage": {
            "status": "error", "error": {"message": "Invalid API key"}}})))


# ------------------------------------------------------------------ Bandsintown
def bit_event(n=1, **over):
    event = {"id": "bit%d" % n, "datetime": day() + "T20:00:00", "url": "https://www.bandsintown.com/e/%d" % n,
             "venue": {"name": "The Fleece", "city": "Bristol", "region": "ENG", "country": "United Kingdom",
                       "latitude": "51.4545", "longitude": "-2.5879"},
             "offers": [{"type": "Tickets", "url": "https://tix.example/%d" % n, "status": "available"}],
             "lineup": ["The Hollow Kings"]}
    event.update(over)
    return event


def test_bandsintown_event_is_read_and_tickets_win_over_the_page():
    item = providers.normalise_bandsintown(bit_event(), "The Hollow Kings")
    assert item["source"] == "bandsintown" and item["title"] == "The Hollow Kings" and item["city"] == "Bristol, ENG"
    assert item["event_at"] == day() + " 20:00" and item["ticket_url"] == "https://tix.example/1"
    no_offers = providers.normalise_bandsintown(bit_event(offers=[]), "X")
    assert no_offers["ticket_url"] == "https://www.bandsintown.com/e/1"
    insecure = providers.normalise_bandsintown(bit_event(offers=[{"type": "Tickets", "url": "http://x"}]), "X")
    assert insecure["ticket_url"] == "https://www.bandsintown.com/e/1"


@pytest.mark.parametrize("change", [{"venue": {}}, {"venue": {"latitude": "", "longitude": ""}}, {"datetime": "soon"}])
def test_bandsintown_unusable_events_are_skipped(change):
    assert providers.normalise_bandsintown(bit_event(**change), "X") is None


def test_bandsintown_request_uses_the_artists_own_app_id():
    get = Get([bit_event(1), bit_event(2)])
    items = list(providers.fetch_bandsintown_artist("Mumford & Sons", "my-app-id", get))
    url, provider, _ = get.calls[0]
    assert len(items) == 2 and provider == "Bandsintown"
    assert url.startswith("https://rest.bandsintown.com/artists/Mumford%20%26%20Sons/events?")
    assert params(url) == {"app_id": "my-app-id", "date": "upcoming"}


def test_bandsintown_answers_that_are_not_a_list_mean_no_events():
    for answer in ({"errors": ["Unknown artist"]}, "warn=Not found", None):
        assert list(providers.fetch_bandsintown_artist("Nobody", "id", Get(answer))) == []


def test_artist_names_cannot_break_out_of_the_address():
    url = providers.bandsintown_url("a/b?c#d", "x")
    assert "/artists/a%2Fb%3Fc%23d/events?" in url


# ------------------------------------------------------------------ the shared pieces
def test_clock_and_position_helpers():
    assert providers._clock("2026-10-12 20:30:00") == "20:30" and providers._clock("T9:05") == "09:05"
    assert providers._clock("24:00") is None and providers._clock("") is None and providers._clock(None) is None
    assert providers._position("51.5", "-0.12") == (51.5, -0.12)
    assert providers._position("0", "0") is None and providers._position("x", "1") is None
    assert providers._position(None, None) is None and providers._position("91", "0") is None


def test_the_registry_lists_the_location_providers_in_priority_order():
    assert list(importer.PROVIDERS) == ["ticketmaster", "skiddle", "songkick"]
    assert importer.LABELS["bandsintown"] == "Bandsintown" and importer.LABELS["skiddle"] == "Skiddle"
    assert importer.KEY_SETTINGS["Skiddle"] == "SKIDDLE_API_KEY"
