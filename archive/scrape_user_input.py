from sys import int_info

import requests
from bs4 import BeautifulSoup, Comment
import re
import time
import sys
from datetime import datetime
from dateutil.relativedelta import relativedelta

import gspread
from oauth2client.service_account import ServiceAccountCredentials

RATING_MILESTONES = [600, 800, 1000, 1200, 1400, 1600, 1800, 2000]
ACCESS_FILE = "velvety-rock-453721-q8-9216969d6bf4.json"


def extract_date(text):
    # Get date in YYYY-MM-DD format, which is used on US Chess
    match = re.search(r"\d{4}-\d{2}-\d{2}", text)
    return match.group() if match else None


def months_difference(date1, date2): # Months difference between two dates in YYYY-MM-DD format

    date1 = datetime.strptime(date1, "%Y-%m-%d")
    date2 = datetime.strptime(date2, "%Y-%m-%d")

    delta = relativedelta(date2, date1)
    return delta.years * 12 + delta.months

def calculate_age(date_of_birth, reference_date):
    dob = datetime.strptime(date_of_birth, "%Y-%m-%d")
    ref_date = datetime.strptime(reference_date, "%Y-%m-%d")

    age = ref_date.year - dob.year - ((ref_date.month, ref_date.day) < (dob.month, dob.day))

    return age


def get_uscf_ids(): # Gets USCF IDs directly from Google sheets
    user_input = input("Enter USCF IDs  (separated by spaces, commas, or newlines): ")
    uscf_ids_clean = re.findall(r'\b\d{8}\b', user_input)
    if not uscf_ids_clean:
        print("No valid USCF IDs entered.")

    return uscf_ids_clean




def get_date_of_births(): # Gets DOBs directly from Google sheets
    user_input = input("Enter corresponding DOBs in MM/DD/YYYY format (separated by spaces, commas, or newlines): ")
    dob_list = re.findall(r"\b(\d{1,2})[-/]?(\d{1,2})[-/]?(\d{4})\b", user_input)

    formatted_dobs = [f"{year}-{month.zfill(2)}-{day.zfill(2)}" for month, day, year in dob_list]# Using YYYY-MM-DD formatting for easier use with US Chess
    return formatted_dobs



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


# def get_birth_year(session, uscf_id): # Returns birth year from FIDE player profile (none if no FIDE ID)
#
#     url = f"https://www.uschess.org/msa/MbrDtlMain.php?{uscf_id}" # Navigate to the 'General' page for a player
#     response = session.get(url)
#     soup = BeautifulSoup(response.text, "lxml")
#
#     fide_tag = soup.find("td", string=lambda text: text and "FIDE ID" in text) # Find if the text 'FIDE ID' is present for a player
#     if fide_tag:
#         fide_id = fide_tag.find_next_sibling("td").find("b").text.strip()
#         url = f"https://ratings.fide.com/profile/{fide_id}" # Navigate to a player's fide page to get birth year
#         response = session.get(url)
#         soup = BeautifulSoup(response. text, "lxml")
#
#         birth_year_tag = soup.find("h5", string=lambda text: text and "B-Year" in text)
#
#         if birth_year_tag:
#             birth_year = birth_year_tag.find_next_sibling("p").text.strip()
#             return int(birth_year)
#
#     else:
#         return None


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

            print(f"Player row: {player_row}")
            print(f"Game row: {game_row}")

            games_pattern = re.findall(r"\b[WLD]\s+\d+", game_row)
            print(f"Games_pattern: {games_pattern}")

            for game in games_pattern:
                if 'W' in game:
                    wins_in_tournament += 1
                elif 'L' in game:
                    losses_in_tournament += 1
                else:
                    draws_in_tournament += 1

            games_played = len(games_pattern)

            print(f"Wins: {wins_in_tournament}")
            print(f"Losses: {losses_in_tournament}")
            print(f"Draws: {draws_in_tournament}")

            games_in_tournament += games_played


    return games_in_tournament, wins_in_tournament, draws_in_tournament, losses_in_tournament


def rating_progress_by_months_games_and_age(session, uscf_id, dob, total_tournaments_played, date_of_first_tournament, start_rating):
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

    first_tournament_found = False
    first_tournament_games = 0

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

                if not first_tournament_found:
                    first_tournament_games = games_played
                    first_tournament_found = True

            prev_tournament_url = tournament_url
            adjusted_win_rate = (wins + 0.5 * draws) / games_played

            print(tournament_date, games_played, post_tournament_rating, adjusted_win_rate)

            all_classical_tournaments.append((tournament_date, games_played, post_tournament_rating, adjusted_win_rate))

        total_tournaments_played -= 1
        if total_tournaments_played % 50 == 0: # Go to previous page if all tournaments on current page have been seen
            last_page_index -= 1
            url = f"https://www.uschess.org/msa/MbrDtlTnmtHst.php?{uscf_id}.{last_page_index}"
            response = session.get(url)
            soup = BeautifulSoup(response.text, "lxml")



    # for index, rating in enumerate(RATING_MILESTONES): # Checks if initial rating is already higher than rating milestones
    #     if int(start_rating) >= rating:
    #         rating_reached_by_months[index] = 0
    #         rating_reached_by_games[index] = first_tournament_games
    #         rating_reached_by_age[index] = calculate_age(dob, date_of_first_tournament) # TODO
    #     else:
    #         start_index = index # Next rating milestone to reach
    #         break
    # if rating_reached_by_months[-1] is not None: # If initial rating is higher than all milestones (returns array of zeros)
    #     return rating_reached_by_months, rating_reached_by_games, rating_reached_by_age

    start_index = 0
    for tournament in all_classical_tournaments: # Finds the months needed to reach all rating milestones
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

    for row_index, (uscf_id, dob) in enumerate(zip(uscf_id_list, dob_list), start=52):
        total_tournaments_played = get_tournaments_played(session, uscf_id)

        if total_tournaments_played is None:
            continue

        date_of_first_tournament, initial_rating = get_first_classical_tournament_details(session, uscf_id, total_tournaments_played)
        if date_of_first_tournament is None or initial_rating is None:
            continue

        name = get_name(session, uscf_id)

        age_at_first_tournament = calculate_age(dob, date_of_first_tournament)

        rating_milestones_by_month, rating_milestones_by_games, rating_milestones_by_age, rating_milestones_by_score = rating_progress_by_months_games_and_age(session, uscf_id, dob, total_tournaments_played, date_of_first_tournament, initial_rating)

        print(f"Games needed for : {rating_milestones_by_games}")
        print(f"Months needed for : {rating_milestones_by_month}")
        print(f"Age when : {rating_milestones_by_age}")
        print(f"Score when: {rating_milestones_by_score}")


if __name__ == '__main__':

    uscf_ids = get_uscf_ids()
    dobs = get_date_of_births()

    if len(uscf_ids) != len(dobs):
        print(f"Number of USCF IDs ({len(uscf_ids)}) differs from number of DOBs ({len(dobs)}).")
        sys.exit(1)

    start_time = time.time()
    session = requests.Session()
    scrape(session, uscf_ids, dobs)
    end_time = time.time()
    print(f"Execution Time: {end_time - start_time:.3f} seconds")