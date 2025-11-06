# USCF Rating Progression Scraper

## Functionality
Given a list of **USCF IDs** and **dates of birth** from a Google Sheet, this script:
 - Scrapes each player's US Chess pages
 - Finds the date of their first **over the board (OTB) classical tournament** and **initial rating**
 - Iterates through all OTB classical events until a specified cutoff date to compute for each rating milestone:
    - **Months** needed to reach milestone
    - **Games** needed to reach milestone
    - **Cumulative record** (reported as (wins + 0.5 * draws / total games))
    - **Age** (in years) to reach milestone
 - Writes results back into the same Google Sheet row


## Using the program
This program includes an implementation with Google Sheets API. For the program to be able to read and modify the sheet, it requires service account credentials, see below.  

### Credentials 
- Navigate to https://developers.google.com/workspace/guides/create-credentials#service-account, and follow the instructions in the section labeled 'Create a service account' and 'Create credentials for a service account.'
- Save the given .json as a file in your cloned repository. This will serve as the access file.
- Share your sheet with the email specified in the access file, and make sure to provide editing access. 

### Configurations
- Update config.py with the sheet name and the name of your access file.
- Create a column with USCF IDs and a separate corresponding column with DOBs - update config.py to match.
- Update config.py with the start row (first row of data) and the start column (where you want the data to be)
  <img width="1327" height="914" alt="Screenshot 2025-11-06 at 10 56 16 AM" src="https://github.com/user-attachments/assets/5fe9d7a6-657f-437f-a19a-8186f5762bd2" />
- In the above example, start row = 3, start column = 'D'. 
- Run the script!


