# ImFasterThanYou

Find the chain of race wins that connects any two collegiate runners.

Pick two runners and the site finds the shortest path between them where every link is a win: A beat B at one meet, B beat C at another, and so on until it reaches the second runner. Inspired by https://myteamisbetterthanyourteam.com/ but for collegiate track and cross country results.

Race data comes from [tfrrs.org](https://www.tfrrs.org), which covers collegiate cross country and track & field. Trent Nosky.

## How it works

1. **Scrape.** `scraper.py` pages through the TFRRS meet search to find every meet, then fetches each one. XC meets have all their results on one page. Track meets have one page per event.
2. **Store.** Results go into Postgres as `(meet, event, gender, place, runner)` rows. Runners are keyed on their TFRRS athlete ID.
3. **Search.** A win means finishing ahead of someone in the same race, so it's derived at query time with a self-join instead of stored as an edge. The Flask app runs a breadth-first search over that, optionally limited to XC or track.

The first version stored one edge per pair of finishers. A 200 person race is about 20,000 edges, and a partial dataset already came to over 3 GB. Storing placings instead keeps the full dataset in the low millions of rows.

## Repo layout

```
app.py          Flask app: runner lookup + shortest-path search
db.py           Postgres connection and schema
scraper.py      TFRRS crawler (meet discovery + per-meet scraping)
templates/      Search form and results page
Procfile        gunicorn entry point for Railway
```

## Usage

**1. Set up**

```bash
pip install -r requirements.txt
export DATABASE_URL=postgresql://user:password@host:port/dbname
```

The schema is created on first run.

**2. Scrape**

```bash
python scraper.py
```

Discovers every meet, then scrapes them. It's safe to stop and rerun, since a meet is only marked done after its rows commit. Flags: `--discover-only`, `--scrape-only`, `--workers N`, `--delay SECONDS`, `--limit N`.

If TFRRS returns 8 straight 403s the scraper stops itself. Wait a while, then rerun with fewer workers or a longer delay.

**3. Run the site**

```bash
python app.py
```

For deployment, Railway runs `gunicorn app:app` from the Procfile. Set `DATABASE_URL` on the service.

## Status

Works end to end, but only partly populated.

Scraping is paused. TFRRS rate limits the crawler, and the blocks came sooner each time: about 4,600 meets before the first one, about 75 before the fourth. Meets are scraped in ID order, so what's there skews toward older XC. Track has barely started. A path only exists through meets that have been scraped, so many runner pairs won't connect yet.

## Limitations

- Names aren't unique. Lookup takes the first runner whose name matches.
- Runners without a TFRRS profile link (mostly "Unattached") are matched by name and team, so one person can show up twice.
- The search runs one query per runner it expands. It's fine on small data and hasn't been tested at full size.
- The only filter is XC vs track. No event, gender, or division filters yet.
- Championship track results have several time columns with no reliable way to pick the final, so time is left blank for those. Placing is still used.
- A win is just a better place in the same race. It says nothing about effort or conditions.
