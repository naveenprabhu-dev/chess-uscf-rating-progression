# Chess Rating Progression Scraper

A small Flask web app that shows how quickly chess players climbed the rating
ladder. Enter one or two players (US Chess or FIDE) and the app scrapes their
rating history to report, for each rating milestone, how many **months**,
**games**, and at what **age** the player first reached it — plus their
cumulative **score percentage** at that point.

> This started as a CLI / Google Sheets script and is now a website. The old
> scripts are kept in `archive/` for reference only.

## Features

- **Two sources:** US Chess (uschess.org) and FIDE (ratings.fide.com). One
  active source per session, switchable in the UI.
- **Per-milestone insights:** months, games, age, and cumulative score % to
  reach each configurable rating milestone.
- **Configurable milestones:** per-source rating ladders, adjustable per
  session (no accounts needed).
- **Compare players:** view two players side by side, including charts.
- **SQLite caching:** scraped rating timelines are cached locally so repeated
  views don't hammer US Chess / FIDE. A manual refresh button re-scrapes.
- **Save a small library:** star players to keep them handy across the session.

## Requirements

- Python 3.12
- Dependencies: Flask, requests, beautifulsoup4, lxml, python-dateutil, curl_cffi

## Setup & running

Using conda (recommended — pins the exact dependency versions):

```bash
conda env create -f environment.yml
conda activate ccc-webscraper
python run.py
```

Or with pip:

```bash
pip install flask requests beautifulsoup4 lxml python-dateutil curl_cffi
python run.py
```

Then open **http://localhost:5050**.

> Note: port `5000` is used by macOS AirPlay Receiver, so the app runs on
> `5050`.

## Usage

1. Open the app and pick a source (US Chess or FIDE).
2. Enter a player's ID and date of birth (`MM/DD/YYYY`). You can analyze up to
   two players at once.
3. View the milestone table and charts. Use **Refresh** to re-scrape a player,
   or **Compare** to see two players together.
4. Adjust the rating milestones for the session from the settings if you want a
   different ladder.

The local SQLite database lives in `instance/` (gitignored) and is created
automatically on first run.

## Project layout

```
scraper/      # framework-agnostic scraping/parsing core (USCF + FIDE)
webapp/       # Flask app (routes, templates, SQLite cache)
config.py     # default rating ladders and limits
run.py        # entry point — serves on http://localhost:5050
docs/         # scraping internals and notes
specs/        # design specs / plans
archive/      # old CLI + Google Sheets scripts (reference only)
instance/     # gitignored — SQLite DB lives here
```

See [`CLAUDE.md`](CLAUDE.md) and [`docs/scraping.md`](docs/scraping.md) for
implementation details.
