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
