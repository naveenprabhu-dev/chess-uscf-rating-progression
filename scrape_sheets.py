import requests
from bs4 import BeautifulSoup, Comment
import re
import time
import sys
from datetime import datetime
from dateutil.relativedelta import relativedelta

import gspread 
from gspread.utils import a1_to_rowcol, rowcol_to_a1
from oauth2client.service_account import ServiceAccountCredentials

RATING_MILESTONES = [400, 600, 800, 1000, 1200, 1400, 1600, 1800, 2000, 2200]
ACCESS_FILE = " " # Input access file name here
SHEET_NAME = " " # Input sheet name here

START_ROW = None # Input first row in sheets with player ID/birthdate
START_COLUMN = None # Input first empty column (int) to start recording the USCF data

DOB_COLUMN = None # Column that contains DOBs
USCF_ID_COLUMN = None # Column that contains USCF IDs



def extract_date(text):
    """
    Get date in YYYY-MM-DD format, which is used on US Chess
    """
    match = re.search(r"\d{4}-\d{2}-\d{2}", text)
    return match.group() if match else None


def months_difference(date1, date2): 
    """
    Months difference between two dates in YYYY-MM-DD format
    """

    date1 = datetime.strptime(date1, "%Y-%m-%d")
    date2 = datetime.strptime(date2, "%Y-%m-%d")

    delta = relativedelta(date2, date1)
    return delta.years * 12 + delta.months

def calculate_age(date_of_birth, reference_date):
    dob = datetime.strptime(date_of_birth, "%m/%d/%Y")
    ref_date = datetime.strptime(reference_date, "%Y-%m-%d")

    age = ref_date.year - dob.year - ((ref_date.month, ref_date.day) < (dob.month, dob.day))

    return age

def _col_index(col):
    """Accept 'C' or 3; return 1-based int index (C->3)."""
    if isinstance(col, int):
        if col < 1:
            raise ValueError("Column indices are 1-based (>=1).")
        return col
    if isinstance(col, str):
        return a1_to_rowcol(f"{col.upper()}1")[1]
    raise TypeError("Column must be an int (1-based) or a letter like 'C'.")

def _cell_a1(row, col):
    """Row=int, col can be 'F' or 6 -> A1 cell like 'F12'."""
    return rowcol_to_a1(row, _col_index(col))

def _read_column_until_blank(sheet, col, start_row):
    """Read values from a column starting at start_row (1-based) until first blank."""
    idx = _col_index(col)
    # gspread.col_values(idx) returns the entire column starting at row 1 as a list.
    # Convert start_row (1-based) -> list index (0-based): start_row-1
    values = sheet.col_values(idx)[start_row-1:]
    out = []
    for v in values:
        if v is None or v == "":
            break
        out.append(v)
    return out

def _validate_config():
    problems = []
    for name, val in [
        ("ACCESS_FILE", ACCESS_FILE),
        ("SHEET_NAME", SHEET_NAME),
        ("START_ROW", START_ROW),
        ("START_COLUMN", START_COLUMN),
        ("DOB_COLUMN", DOB_COLUMN),
        ("USCF_ID_COLUMN", USCF_ID_COLUMN),
    ]:
        if val in (None, ""):
            problems.append(f"{name} is not set.")
    # also resolve columns early to surface errors
    try: _ = _col_index(DOB_COLUMN)
    except Exception as e: problems.append(f"DOB_COLUMN invalid: {e}")
    try: _ = _col_index(USCF_ID_COLUMN)
    except Exception as e: problems.append(f"USCF_ID_COLUMN invalid: {e}")
    try: _ = _col_index(START_COLUMN)
    except Exception as e: problems.append(f"START_COLUMN invalid: {e}")
    if not isinstance(START_ROW, int) or START_ROW < 1:
        problems.append("START_ROW must be a 1-based positive integer.")
    if problems:
        raise RuntimeError("Config error(s):\n  - " + "\n  - ".join(problems))

def get_uscf_ids(data_sheet):
    """Gets USCF IDs from the configured column, starting at START_ROW."""
    return _read_column_until_blank(data_sheet, USCF_ID_COLUMN, START_ROW)

def get_dobs(data_sheet):
    """Gets DOBs from the configured column, starting at START_ROW."""
    return _read_column_until_blank(data_sheet, DOB_COLUMN, START_ROW)


def get_tournaments_played(session, uscf_id): # Returns total tournaments played (includes classical/rapid/blitz/online), used for navigating through user's tournaments
    url = f"https://www.uschess.org/msa/MbrDtlTnmtHst.php?{uscf_id}"
    response = session.get(url, timeout=5)
    soup = BeautifulSoup(response.text, 'lxml')

    no_id = soup.find("b", string=lambda text: text and "Could not retrieve " in text)
    if no_id:
        print(f"USCF ID: {uscf_id} does not exist.")
        sys.exit(1)

    no_tournaments = soup.find("b", string=lambda text: text and "There are no tournament results" in text)
    if no_tournaments:
        print(f"USCF ID: {uscf_id} has played no tournaments. ")
        return None

    b_tag_tournaments_played = soup.find("b",
                                         string=lambda text: text and "Events for this player since late 1991" in text)
    if b_tag_tournaments_played:
        matches = re.findall(r'\d+', b_tag_tournaments_played.text)
        tournaments_played = matches[1]
        return int(tournaments_played)


def get_name(session, uscf_id): # Returns name for any player
    url = f"https://www.uschess.org/msa/MbrDtlMain.php?{uscf_id}"
    response = session.get(url)
    soup = BeautifulSoup(response.text, "lxml")
    name = soup.find("b").text.split(" ", 1)[1]

    return name


def get_first_classical_tournament_details(session, uscf_id, total_tournaments_played): # Returns the date of first OTB classical tournament and initial rating achieved
    initial_rating = None
    first_tournament_date = None
    last_page = (total_tournaments_played - 1) // 50 + 1
    url = f"https://www.uschess.org/msa/MbrDtlTnmtHst.php?{uscf_id}.{last_page}"
    response = session.get(url)
    soup = BeautifulSoup(response.text, "lxml")

    while total_tournaments_played > 0:
        comment = soup.find(string=lambda text: isinstance(text, Comment) and f"Detail: {total_tournaments_played}" in text) # Go to this tournament's details
        tournament_row = comment.find_next_sibling("tr") # Get the next <tr> tag (enter the information for this tournament)
        td_tags = tournament_row.find_all("td")
        classical_td_text = td_tags[2].text.strip() # Get the classical rating change information (located in the third <td> tag)

        if "=>" in classical_td_text and "ONL" not in classical_td_text: # If it is a classical tournament that is not online
            first_tournament_date = extract_date(td_tags[0].text)
            initial_rating = classical_td_text.split("=>")[-1].strip()

            if "P" in initial_rating:
                initial_rating = initial_rating.split(" ")[0].strip()
                initial_rating = int(initial_rating)

            return first_tournament_date, initial_rating
        total_tournaments_played -= 1
        if total_tournaments_played % 50 == 0:
            last_page -= 1
            url = f"https://www.uschess.org/msa/MbrDtlTnmtHst.php?{uscf_id}.{last_page}"
            response = session.get(url)
            soup = BeautifulSoup(response.text, "lxml")




    print(f"Could not find any classical OTB tournaments for USCF ID: {uscf_id}")
    return first_tournament_date, initial_rating

def games_played_in_tournament(session, uscf_id, tournament_url):
    base_url = "https://www.uschess.org/msa/"
    modified_url = tournament_url.split("-")[0] + ".0"
    new_url = base_url + modified_url
    response = session.get(new_url)
    soup = BeautifulSoup(response.text, "lxml")
    sections = soup.find_all("pre")

    games_in_tournament = 0
    wins_in_tournament = 0
    draws_in_tournament = 0
    losses_in_tournament = 0

    for section in sections:
        section_text = section.get_text()
        lines = section_text.splitlines()


        pattern = rf"(.+\|\s*{uscf_id}\s*/\s*R:.*->.*)"
        match = re.search(pattern, section_text)

        if match:
            player_row = match.group(1)
            index = lines.index(player_row)
            game_row = lines[index - 1]


            games_pattern = re.findall(r"\b[WLD]\s+\d+", game_row)

            for game in games_pattern:
                if 'W' in game:
                    wins_in_tournament += 1
                elif 'L' in game:
                    losses_in_tournament += 1
                else:
                    draws_in_tournament += 1

            games_played = len(games_pattern)


            games_in_tournament += games_played



    return games_in_tournament, wins_in_tournament, draws_in_tournament, losses_in_tournament


def rating_progress_by_months_games_and_age(session, uscf_id, dob, total_tournaments_played, date_of_first_tournament, start_rating):
    cutoff = datetime.strptime('2025-05-16', '%Y-%m-%d')
    games_played = 0
    wins = 0
    draws = 0
    losses = 0
    all_classical_tournaments = []
    rating_reached_by_months = [None for i in range(len(RATING_MILESTONES))]
    rating_reached_by_games = [None for i in range(len(RATING_MILESTONES))]
    rating_reached_by_age = [None for i in range(len(RATING_MILESTONES))]
    rating_reached_by_score = [None for i in range(len(RATING_MILESTONES))]

    last_page_index = (total_tournaments_played - 1) // 50 + 1

    # first_tournament_found = False
    # first_tournament_games = 0

    url = f"https://www.uschess.org/msa/MbrDtlTnmtHst.php?{uscf_id}.{last_page_index}" # Earliest tournaments to be found on US Chess
    response = session.get(url)
    soup = BeautifulSoup(response.text, "lxml")

    prev_tournament_url = None

    while total_tournaments_played > 0:
        comment = soup.find(string=lambda text: isinstance(text,
                                                           Comment) and f"Detail: {total_tournaments_played}" in text)  # Go to this tournament's details
        tournament_row = comment.find_next_sibling(
            "tr")  # Get the next <tr> tag (the information for this tournament)
        td_tags = tournament_row.find_all("td")
        classical_td_text = td_tags[
            2].text.strip()  # Get the classical rating change information (located in the third <td> tag)


        if "=>" in classical_td_text and "ONL" not in classical_td_text:  # If it is a classical tournament that is not online
            tournament_date = extract_date(td_tags[0].text) # Get tournament date
            tournament_dt = datetime.strptime(tournament_date, "%Y-%m-%d")
            if tournament_dt > cutoff:
                break
            post_tournament_rating = classical_td_text.split("=>")[-1].strip() # Get post tournament rating

            if "P" in post_tournament_rating:
                post_tournament_rating = post_tournament_rating.split(" ")[0].strip()

            tournament_url = td_tags[1].find("a")["href"]

            if tournament_url != prev_tournament_url: # Make sure it's not two sections of the same tournament to avoid double counting
                tournament_results = games_played_in_tournament(session, uscf_id, tournament_url)
                games_played += tournament_results[0]
                wins += tournament_results[1]
                draws += tournament_results[2]
                losses += tournament_results[3]
                # if not first_tournament_found:
                #     first_tournament_games = games_played
                #     first_tournament_found = True

            prev_tournament_url = tournament_url

            if games_played != 0:  # For the one guy that played a tournament, took 2 byes and withdrew
                adjusted_win_rate = (wins + 0.5 * draws) / games_played


            print(tournament_date, games_played, post_tournament_rating)
            if games_played != 0:
                 all_classical_tournaments.append((tournament_date, games_played, post_tournament_rating, adjusted_win_rate))

        total_tournaments_played -= 1
        if total_tournaments_played % 50 == 0: # Go to previous page if all tournaments on current page have been seen
            last_page_index -= 1
            url = f"https://www.uschess.org/msa/MbrDtlTnmtHst.php?{uscf_id}.{last_page_index}"
            response = session.get(url)
            soup = BeautifulSoup(response.text, "lxml")

    start_index = 0
    for tournament in all_classical_tournaments:  # Finds the months needed to reach all rating milestones
        while int(tournament[2]) >= RATING_MILESTONES[start_index]:
            print(tournament)
            rating_reached_by_months[start_index] = months_difference(date_of_first_tournament, tournament[0])
            rating_reached_by_games[start_index] = tournament[1]
            rating_reached_by_age[start_index] = calculate_age(dob, tournament[0])
            rating_reached_by_score[start_index] = tournament[3]

            if start_index != len(RATING_MILESTONES) - 1:
                start_index += 1

            if rating_reached_by_months[-1] is not None:
                break
        if rating_reached_by_months[-1] is not None:
            break


    return rating_reached_by_months, rating_reached_by_games, rating_reached_by_age, rating_reached_by_score

def scrape(session, uscf_id_list, dob_list):

    for row_index, (uscf_id, dob) in enumerate(zip(uscf_id_list, dob_list), start=START_ROW):
        total_tournaments_played = get_tournaments_played(session, uscf_id)

        if total_tournaments_played is None:
            continue

        date_of_first_tournament, initial_rating = get_first_classical_tournament_details(session, uscf_id, total_tournaments_played)
        if date_of_first_tournament is None or initial_rating is None:
            continue

        name = get_name(session, uscf_id)
        print(name)

        age_at_first_tournament = calculate_age(dob, date_of_first_tournament)

        rating_milestones_by_month, rating_milestones_by_games, rating_milestones_by_age, rating_milestones_by_score = rating_progress_by_months_games_and_age(session, uscf_id, dob, total_tournaments_played, date_of_first_tournament, initial_rating
                               )

        data_row = [date_of_first_tournament] + [initial_rating] + [age_at_first_tournament] +  rating_milestones_by_month + rating_milestones_by_games + rating_milestones_by_score + rating_milestones_by_age
        
        cell_range = _cell_a1(row_index, START_COLUMN)

        sheet.update(range_name=cell_range, values=[data_row])

        print(f"Games needed for : {rating_milestones_by_games}")
        print(f"Months needed for : {rating_milestones_by_month}")
        print(f"Age when : {rating_milestones_by_age}")
        print(f"Score when : {rating_milestones_by_score}")


if __name__ == '__main__':
    _validate_config()
    scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]

    creds = ServiceAccountCredentials.from_json_keyfile_name(ACCESS_FILE, scope)

    client = gspread.authorize(creds)

    sheet = client.open(SHEET_NAME).sheet1

    uscf_ids = get_uscf_ids(sheet)
    dobs = get_dobs(sheet)

    # Open Google Sheet

    if len(uscf_ids) != len(dobs):
        print(f"Number of USCF IDs ({len(uscf_ids)}) differs from number of DOBs ({len(dobs)}).")
        sys.exit(1)

    start_time = time.time()
    session = requests.Session()
    scrape(session, uscf_ids, dobs)
    end_time = time.time()
    print(f"Execution Time: {end_time - start_time:.3f} seconds")