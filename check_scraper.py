"""Offline self-check for scraper.py parsing/retry logic. Run: python check_scraper.py"""
import requests
from bs4 import BeautifulSoup

import scraper

scraper.REQUEST_DELAY = 0
scraper.time.sleep = lambda s: None  # no real waits in backoff

XC_PAGE = """
<div class="custom-table-title"><h3>Men's 8k Individual Results (8000m)</h3></div>
<table><thead><tr><th>PL</th><th>NAME</th><th>YEAR</th><th>TEAM</th><th>TIME</th></tr></thead>
<tbody>
<tr><td>1</td><td><a href="/athletes/123/Foo.html">Foo, Bob</a></td><td>SR</td><td>State</td><td>24:01.5</td></tr>
<tr><td>DNF</td><td><a href="/athletes/124/Bar.html">Bar, Al</a></td><td>JR</td><td>State</td><td>DNF</td></tr>
</tbody></table>
<div class="custom-table-title"><h3>Men's 8k Team Results</h3></div>
<table><thead><tr><th>PL</th><th>Team</th><th>Score</th><th>Total Time</th></tr></thead>
<tbody><tr><td>1</td><td>State</td><td>30</td><td>2:00:00</td></tr></tbody></table>
<span class="race-title">Women's 6k</span>
<table><tr><th>PL</th><th>NAME</th><th>TIME</th></tr>
<tr><td>1</td><td><a href="https://www.tfrrs.org/athletes/555/X.html">X, Y</a></td><td>20:00.1</td></tr></table>
"""


TRACK_PAGE = """
<div class="custom-title"><h3>Men's 100 Meters Preliminaries</h3></div>
<table><thead><tr><th>PL</th><th>NAME</th><th>TEAM</th><th>TIME</th></tr></thead>
<tbody>
<tr><td>1</td><td><a href="/athletes/201/RunnerA.html">Runner, A</a></td><td>State</td><td>10.45</td></tr>
<tr><td>2</td><td><a href="/athletes/202/RunnerB.html">Runner, B</a></td><td>Tech</td><td>10.60</td></tr>
</tbody></table>

<div class="custom-title"><h3>Men's 100 Meters Finals</h3></div>
<table><thead><tr><th>PL</th><th>NAME</th><th>TEAM</th><th>TIME</th></tr></thead>
<tbody>
<tr><td>1</td><td><a href="/athletes/202/RunnerB.html">Runner, B</a></td><td>Tech</td><td>10.35</td></tr>
<tr><td>2</td><td><a href="/athletes/201/RunnerA.html">Runner, A</a></td><td>State</td><td>10.40</td></tr>
</tbody></table>

<div class="custom-title"><h3>Women Long Jump</h3></div>
<table><thead><tr><th>PL</th><th>NAME</th><th>TEAM</th><th>MARK</th></tr></thead>
<tbody>
<tr><td>1</td><td><a href="/athletes/301/JumperA.html">Jumper, A</a></td><td>Uni</td><td>6.25m</td></tr>
</tbody></table>

<div class="custom-title"><h3>Men's 4 x 400m Relay</h3></div>
<table><thead><tr><th>PL</th><th>NAME</th><th>TEAM</th><th>TIME</th></tr></thead>
<tbody>
<tr><td>1</td><td>State 'A'</td><td>State</td><td>3:10.15</td></tr>
</tbody></table>
"""


class FakeResp:
    def __init__(self, status, text=""):
        self.status_code, self.text, self.headers = status, text, {}
        self.ok = status < 400

    def raise_for_status(self):
        if not self.ok:
            raise requests.HTTPError(str(self.status_code))


class FakeSession:
    def __init__(self, *responses):
        self.responses = list(responses)

    def get(self, url, timeout):
        r = self.responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


# XC parsing: individual tables kept (incl. one without thead/team), team table dropped, DNF row dropped
races = scraper.scrape_xc_meet(FakeSession(FakeResp(200, XC_PAGE)), "u")
assert [(r["gender"], r["event"]) for r in races] == [("Men", "8000m"), ("Women", "XC")], races
assert races[0]["rows"] == [{"place": 1, "athlete_id": 123, "name": "Foo, Bob", "team": "State", "time": "24:01.5"}]
assert races[1]["rows"][0]["athlete_id"] == 555 and races[1]["rows"][0]["team"] == ""

# Discovery rows: XC kept, track marked as track, external dropped, short rows tolerated
row = lambda html: BeautifulSoup(f"<table><tr>{html}</tr></table>", "html.parser").tr
xc = scraper.parse_search_row(row('<td>10/12/2024</td><td><a href="/results/xc/999/Big_Meet">Big</a></td>'))
assert xc["meet_id"] == 999 and xc["sport"] == "xc" and xc["state"] is None and xc["url"].startswith("https://www.tfrrs.org/")
track = scraper.parse_search_row(row('<td>10/12/2024</td><td><a href="/results/888/Track">T</a></td><td>TF</td><td>NY</td>'))
assert track["meet_id"] == 888 and track["sport"] == "track" and track["state"] == "NY" and track["url"].startswith("https://www.tfrrs.org/")
assert scraper.parse_search_row(row('<td>x</td><td><a href="https://evil.example/results/xc/1/x">E</a></td>')) is None

# Track parsing: prelims separated from finals, field events extracted with MARK, relays skipped
track_races = scraper.scrape_track_meet(FakeSession(FakeResp(200, TRACK_PAGE)), "u")
assert len(track_races) == 3, f"Expected 3 individual races (relays skipped), got {len(track_races)}"
assert [(r["gender"], r["event"]) for r in track_races] == [
    ("Men", "100 Meters Preliminaries"),
    ("Men", "100 Meters Finals"),
    ("Women", "Long Jump"),
]
# Verify runner times / marks
assert track_races[0]["rows"][0]["time"] == "10.45"
assert track_races[1]["rows"][0]["time"] == "10.35"
assert track_races[2]["rows"][0]["time"] == "6.25m"
assert track_races[2]["rows"][0]["athlete_id"] == 301

# Discovery stops when the server repeats the last page instead of returning an empty one
page = '<table><tbody><tr><td>10/12/2024</td><td><a href="/results/xc/1/A">A</a></td></tr></tbody></table>'
saved = []
scraper.save_discovered_page = saved.append
scraper.discover_meets(FakeSession(FakeResp(200, page), FakeResp(200, page)))
assert len(saved) == 1

# Year parsing for --since-year
assert scraper.meet_year("10/12/2024") == 2024
assert scraper.meet_year("10/12/19") == 2019
assert scraper.meet_year("Oct 11-12, 2023") == 2023
assert scraper.meet_year("TBD") is None
assert scraper.meet_year("Nov 20, 1999") == 1999

# fetch_soup: recovers from 403/429/connection errors, blocks only after all retries, no retry on 404
assert scraper.fetch_soup(FakeSession(FakeResp(403), FakeResp(429), FakeResp(200, "<p>ok</p>")), "u").p.text == "ok"
assert scraper.fetch_soup(FakeSession(requests.ConnectionError(), FakeResp(200, "<p>ok</p>")), "u").p
for responses, exc in [((FakeResp(403),) * 3, scraper.BlockedError), ((FakeResp(404), FakeResp(200)), requests.HTTPError)]:
    try:
        scraper.fetch_soup(FakeSession(*responses), "u")
        raise AssertionError("expected " + exc.__name__)
    except exc:
        pass

print("all scraper checks passed")

