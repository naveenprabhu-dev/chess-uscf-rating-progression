**Functionality**
Given a list of USCF IDs and dates of birth from a Google Sheet, this script:
Scrapes each player's US Chess pages
Finds their first over the board (OTB) classical tournament and initial rating
Iterates through all OTB classical events until a specified cutoff date to compute for each rating milestone:
    Total months needed to reach milestone
    Total games needed to reach milestone
    Cumulative wins/draws/losses (reported as (wins + 0.5 * draws / total games))
    Age in years to reach milestone
Writes results back into the same Google Sheet row

    
    

**Using the program**

