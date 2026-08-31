import argparse
import math
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
from bs4 import BeautifulSoup, Tag

import db

BASE = "https://www.tfrrs.org"
SEARCH_URL = BASE + "/results_search.html"
REQUEST_DELAY = 0.2
TIME_RE = re.compile(r"\d{1,2}:\d{2}\.\d{1,2}|\b\d{1,3}\.\d{1,2}\b")
ATHLETE_ID_RE = re.compile(r"tfrrs\.org/athletes/(\d+)/")
MEET_ID_RE = re.compile(r"/results/(?:xc/)?(\d+)/")
EVENT_LINK_RE_TEMPLATE = r"/results/{meet_id}/(\d+)/"
GENDER_RE = re.compile(r"/(Mens|Womens)-", re.IGNORECASE)
TOTAL_RE = re.compile(r"of\s+([\d,]+)\s+in total", re.IGNORECASE)


def make_session():
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": "ImFasterThanYou-research-scraper/1.0 "
            "(non-commercial student project; contact via github)"
        }
    )
    return session


def fetch_soup(session, url):
    resp = session.get(url, timeout=20)
    resp.raise_for_status()
    time.sleep(REQUEST_DELAY)
    return BeautifulSoup(resp.text, "html.parser")


def absolute_url(href):
    if href.startswith("http"):
        return href
    return BASE + href


# ---------------------------------------------------------------------------
# Meet discovery
# ---------------------------------------------------------------------------
def discover_meets(session, conn, start_page=1, end_page=None):
    soup = fetch_soup(session, f"{SEARCH_URL}?page={start_page}")
    if end_page is None:
        match = TOTAL_RE.search(soup.get_text())
        total = int(match.group(1).replace(",", "")) if match else 0
        end_page = math.ceil(total / 30) if total else start_page

    page = start_page
    while page <= end_page:
        if page != start_page:
            soup = fetch_soup(session, f"{SEARCH_URL}?page={page}")
        rows = soup.select("table tbody tr")
        if not rows:
            break

        with conn.cursor() as cur:
            for tr in rows:
                meet = parse_search_row(tr)
                if meet:
                    cur.execute(
                        "INSERT INTO meets (meet_id, name, date, sport, state, url, scraped) "
                        "VALUES (%(meet_id)s, %(name)s, %(date)s, %(sport)s, %(state)s, %(url)s, FALSE) "
                        "ON CONFLICT (meet_id) DO NOTHING",
                        meet,
                    )
        conn.commit()
        print(f"discovered page {page}/{end_page}")
        page += 1


def parse_search_row(tr):
    tds = tr.find_all("td")
    if len(tds) < 4:
        return None
    link = tds[1].find("a")
    if not link or not link.get("href"):
        return None
    href = link["href"]
    id_match = MEET_ID_RE.search(href)
    if not id_match:
        return None

    sport_text = tds[2].get_text(strip=True)
    return {
        "meet_id": int(id_match.group(1)),
        "name": link.get_text(strip=True),
        "date": tds[0].get_text(strip=True),
        "sport": "xc" if "cross country" in sport_text.lower() else "track",
        "state": tds[3].get_text(strip=True),
        "url": absolute_url(href),
    }


# ---------------------------------------------------------------------------
# Row / runner parsing shared by XC and track tables
# ---------------------------------------------------------------------------
def header_indices(table):
    thead = table.find("thead")
    if not thead:
        return None
    idx = {}
    time_indices = []
    for i, th in enumerate(thead.find_all("th")):
        text = th.get_text(strip=True).upper()
        if text.startswith("PL"):
            idx.setdefault("place", i)
        elif text == "NAME":
            idx.setdefault("name", i)
        elif text == "TEAM":
            idx.setdefault("team", i)
        elif text == "TIME":
            time_indices.append(i)
    if {"place", "name", "team"} <= idx.keys():
        idx["time_indices"] = time_indices
        return idx
    return None


def parse_result_rows(table):
    idx = header_indices(table)
    if idx is None:
        return []
    tbody = table.find("tbody")
    if not tbody:
        return []

    time_indices = idx["time_indices"]
    max_idx = max([idx["place"], idx["name"], idx["team"]] + time_indices)

    rows = []
    for tr in tbody.find_all("tr"):
        tds = tr.find_all("td")
        if len(tds) <= max_idx:
            continue

        place_text = tds[idx["place"]].get_text(strip=True)
        place_match = re.match(r"\d+", place_text)
        if not place_match:
            continue

        name_cell = tds[idx["name"]]
        link = name_cell.find("a", href=True)
        athlete_id = None
        if link:
            athlete_match = ATHLETE_ID_RE.search(link["href"])
            if athlete_match:
                athlete_id = int(athlete_match.group(1))
        name = name_cell.get_text(strip=True)
        if not name:
            continue

        team = tds[idx["team"]].get_text(strip=True)

        # Championship meets report one "TIME"-labeled column per round
        # (prelim/semi/final) with no reliable ordering as to which is the
        # final result (verified against live data: sometimes first,
        # sometimes last). Rather than guess and risk a wildly wrong time,
        # only record it when the header names exactly one TIME column.
        row_time = None
        if len(time_indices) == 1:
            text = tds[time_indices[0]].get_text(strip=True)
            if TIME_RE.fullmatch(text):
                row_time = text

        rows.append(
            {
                "place": int(place_match.group()),
                "athlete_id": athlete_id,
                "name": name,
                "team": team,
                "time": row_time,
            }
        )
    return rows


def heading_for_table(table):
    el = table
    for _ in range(6):
        if el is None:
            break
        sib = el.previous_sibling
        while sib is not None:
            if isinstance(sib, Tag):
                if sib.name == "h3":
                    return sib.get_text(strip=True)
                h3 = sib.find("h3")
                if h3:
                    return h3.get_text(strip=True)
            sib = sib.previous_sibling
        el = el.parent
    return None


# ---------------------------------------------------------------------------
# XC meets: individual-results tables live directly on the meet page
# ---------------------------------------------------------------------------
def scrape_xc_meet(session, meet_url):
    soup = fetch_soup(session, meet_url)
    races = []
    for table in soup.find_all("table"):
        heading = heading_for_table(table)
        if not heading or "individual results" not in heading.lower():
            continue

        lower = heading.lower()
        if lower.startswith("women"):
            gender = "Women"
        elif lower.startswith("men"):
            gender = "Men"
        else:
            gender = "Unknown"

        distance_match = re.search(r"\(([^)]+)\)", heading)
        event = distance_match.group(1) if distance_match else "XC"

        rows = parse_result_rows(table)
        if rows:
            races.append({"event": event, "gender": gender, "rows": rows})
    return races


# ---------------------------------------------------------------------------
# Track meets: meet page links out to one results page per event
# ---------------------------------------------------------------------------
def scrape_track_meet(session, meet_id, meet_url):
    soup = fetch_soup(session, meet_url)
    event_link_re = re.compile(EVENT_LINK_RE_TEMPLATE.format(meet_id=meet_id))

    seen_hrefs = set()
    events = []
    for link in soup.find_all("a", href=True):
        href = link["href"]
        if href in seen_hrefs or not event_link_re.search(href):
            continue
        gender_match = GENDER_RE.search(href)
        if not gender_match:
            continue
        seen_hrefs.add(href)
        events.append(
            {
                "url": absolute_url(href),
                "gender": "Men" if gender_match.group(1).lower() == "mens" else "Women",
                "event": link.get_text(strip=True),
            }
        )

    races = []
    for ev in events:
        try:
            event_soup = fetch_soup(session, ev["url"])
        except requests.RequestException as exc:
            print(f"  failed to fetch event {ev['url']}: {exc}")
            continue
        table = event_soup.find("table")
        if table is None:
            continue
        rows = parse_result_rows(table)
        if rows:
            races.append({"event": ev["event"], "gender": ev["gender"], "rows": rows})
    return races


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------
def resolve_runner_id(cur, athlete_id, name, team):
    if athlete_id is not None:
        cur.execute(
            "INSERT INTO runners (runner_id, name) VALUES (%s, %s) "
            "ON CONFLICT (runner_id) DO UPDATE SET name = EXCLUDED.name",
            (athlete_id, name),
        )
        return athlete_id

    fallback_key = f"{name}|{team}"
    cur.execute("SELECT nextval('runner_fallback_seq')")
    candidate_id = cur.fetchone()[0]
    cur.execute(
        "INSERT INTO runner_fallback_keys (fallback_key, runner_id) VALUES (%s, %s) "
        "ON CONFLICT (fallback_key) DO NOTHING RETURNING runner_id",
        (fallback_key, candidate_id),
    )
    row = cur.fetchone()
    if row:
        cur.execute("INSERT INTO runners (runner_id, name) VALUES (%s, %s)", (candidate_id, name))
        return candidate_id
    cur.execute("SELECT runner_id FROM runner_fallback_keys WHERE fallback_key = %s", (fallback_key,))
    return cur.fetchone()[0]


def save_races(conn, meet_id, races):
    with conn.cursor() as cur:
        for race in races:
            for row in race["rows"]:
                runner_id = resolve_runner_id(cur, row["athlete_id"], row["name"], row["team"])
                cur.execute(
                    "INSERT INTO results (meet_id, event, gender, place, runner_id, team, time) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s)",
                    (meet_id, race["event"], race["gender"], row["place"], runner_id, row["team"], row["time"]),
                )
        cur.execute("UPDATE meets SET scraped = TRUE WHERE meet_id = %s", (meet_id,))
    conn.commit()


def scrape_meet(session, meet_id, sport, url):
    if sport == "xc":
        return scrape_xc_meet(session, url)
    return scrape_track_meet(session, meet_id, url)


def process_meet(meet_id, sport, url):
    session = make_session()
    conn = db.get_conn()
    try:
        races = scrape_meet(session, meet_id, sport, url)
        save_races(conn, meet_id, races)
        total_rows = sum(len(r["rows"]) for r in races)
        return meet_id, total_rows, None
    except Exception as exc:  # noqa: BLE001 - keep the worker pool alive on any single-meet failure
        return meet_id, 0, str(exc)
    finally:
        conn.close()


def scrape_pending_meets(conn, workers=8, limit=None):
    with conn.cursor() as cur:
        query = "SELECT meet_id, sport, url FROM meets WHERE scraped = FALSE ORDER BY meet_id"
        if limit:
            query += f" LIMIT {int(limit)}"
        cur.execute(query)
        pending = cur.fetchall()

    print(f"{len(pending)} meets pending")
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(process_meet, mid, sport, url): mid for mid, sport, url in pending}
        done = 0
        for future in as_completed(futures):
            meet_id, rows, error = future.result()
            done += 1
            status = f"ERROR: {error}" if error else f"{rows} rows"
            print(f"[{done}/{len(pending)}] meet {meet_id}: {status}")


def main():
    parser = argparse.ArgumentParser(description="Scrape TFRRS meet results into Postgres.")
    parser.add_argument("--discover-only", action="store_true", help="Only run meet discovery.")
    parser.add_argument("--scrape-only", action="store_true", help="Only scrape already-discovered meets.")
    parser.add_argument("--start-page", type=int, default=1)
    parser.add_argument("--end-page", type=int, default=None)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--limit", type=int, default=None, help="Cap number of meets scraped this run.")
    args = parser.parse_args()

    conn = db.get_conn()
    db.init_schema(conn)

    session = make_session()

    if not args.scrape_only:
        discover_meets(session, conn, start_page=args.start_page, end_page=args.end_page)

    if not args.discover_only:
        scrape_pending_meets(conn, workers=args.workers, limit=args.limit)

    conn.close()


if __name__ == "__main__":
    main()
