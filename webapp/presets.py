"""Built-in "quick add" presets on the index page, in two sections: the
current FIDE top 15 (added as FIDE analyses) and top American-raised players
(added as USCF analyses). A player can appear in both sections — same photo,
one ID per section — so e.g. Caruana in the FIDE dropdown adds his FIDE
history and Caruana in the USCF dropdown adds his USCF history.

Every field here is public data:
  * ``uscf_id``  — verified against the player's live US Chess MSA member page
    (https://www.uschess.org/msa/MbrDtlMain.php?<id>); the displayed name matched.
  * ``fide_id``  — verified against FIDE's rating site (ratings.fide.com):
    the USCF section's IDs via player search on 2026-07-01, and the FIDE top-15
    section scraped from FIDE's own top list (profile links) on 2026-07-07,
    birth years cross-checked against the same page.
  * ``dob``      — MM/DD/YYYY, from public sources (Wikipedia / FIDE / news).
  * photo fields — a Creative-Commons-licensed (or CC0/public-domain) portrait
    stored under ``static/players/``, carrying the attribution the CC licenses
    require. Full per-photo credit is also in ``static/players/CREDITS.md``.

Two USCF-section players (Mishra, Woodward) are minors, but well-documented
public figures whose birthdates are central to their record-breaking GM
coverage; only those already-published dates are used. Nothing here is private
personal data, and the app only ever reads the same public USCF/FIDE rating
history it would for any ID a user types in by hand.
"""

# The FIDE standard top 15 (July 2026 list, ratings.fide.com top list).
# Added as FIDE analyses.
FEATURED_FIDE = [
    {
        "name": "Magnus Carlsen",
        "fide_id": "1503014",
        "dob": "11/30/1990",
        "photo": "carlsen.jpg",
        "photo_by": "Miroslav Vajdić",
        "photo_license": "CC BY 4.0",
        "photo_source": "https://commons.wikimedia.org/wiki/File:Magnus_Carlsen_in_2025.jpg",
    },
    {
        "name": "Fabiano Caruana",
        "fide_id": "2020009",
        "dob": "07/30/1992",
        "photo": "caruana.jpg",
        "photo_by": "Frans Peeters",
        "photo_license": "CC BY-SA 2.0",
        "photo_source": "https://commons.wikimedia.org/wiki/File:Fabiano_Caruana_in_2025.jpg",
    },
    {
        "name": "Hikaru Nakamura",
        "fide_id": "2016192",
        "dob": "12/09/1987",
        "photo": "nakamura.jpg",
        "photo_by": "Andreas Kontokanis",
        "photo_license": "CC BY-SA 2.0",
        "photo_source": "https://commons.wikimedia.org/wiki/File:Nakamura_Hikaru_(29290269410)_(cropped).jpg",
    },
    {
        "name": "Javokhir Sindarov",
        "fide_id": "14205483",
        "dob": "12/08/2005",
        "photo": "sindarov.jpg",
        "photo_by": "MiroJP",
        "photo_license": "CC BY-SA 4.0",
        "photo_source": "https://commons.wikimedia.org/wiki/File:Javokhir_Sindarov_(cropped).jpg",
    },
    {
        "name": "Vincent Keymer",
        "fide_id": "12940690",
        "dob": "11/15/2004",
        "photo": "keymer.jpg",
        "photo_by": "Ahmed0112",
        "photo_license": "CC0",
        "photo_source": "https://commons.wikimedia.org/wiki/File:Vincent_Keymer_2026_Norway_Chess.jpg",
    },
    {
        "name": "Nodirbek Abdusattorov",
        "fide_id": "14204118",
        "dob": "09/18/2004",
        "photo": "abdusattorov.jpg",
        "photo_by": "TheBoburshokh",
        "photo_license": "CC0",
        "photo_source": "https://commons.wikimedia.org/wiki/File:Abdusattorov_Nodirbek_Uzchess_cup_3_masters_(cropped).jpg",
    },
    {
        "name": "Wesley So",
        "fide_id": "5202213",
        "dob": "10/09/1993",
        "photo": "so.jpg",
        "photo_by": "Frans Peeters",
        "photo_license": "CC BY-SA 2.0",
        "photo_source": "https://commons.wikimedia.org/wiki/File:Wesley_So_in_2023.jpg",
    },
    {
        "name": "Anish Giri",
        "fide_id": "24116068",
        "dob": "06/28/1994",
        "photo": "giri.jpg",
        "photo_by": "TheBoburshokh",
        "photo_license": "CC0",
        "photo_source": "https://commons.wikimedia.org/wiki/File:FIDE_Grand_Swiss_2025_Samarkand_Anish_Giri.jpg",
    },
    {
        "name": "Arjun Erigaisi",
        "fide_id": "35009192",
        "dob": "09/03/2003",
        "photo": "erigaisi.jpg",
        "photo_by": "TheBoburshokh",
        "photo_license": "CC0",
        "photo_source": "https://commons.wikimedia.org/wiki/File:Arjun_Erigaisi_Uzchess_cup_3_masters_(cropped).jpg",
    },
    {
        "name": "Wei Yi",
        "fide_id": "8603405",
        "dob": "06/02/1999",
        "photo": "wei.jpg",
        "photo_by": "Frans Peeters",
        "photo_license": "CC BY-SA 2.0",
        "photo_source": "https://commons.wikimedia.org/wiki/File:Wei_Yi_in_2025_(cropped).jpg",
    },
    {
        "name": "R. Praggnanandhaa",
        "fide_id": "25059530",
        "dob": "08/10/2005",
        "photo": "praggnanandhaa.jpg",
        "photo_by": "Frans Peeters",
        "photo_license": "CC BY-SA 2.0",
        "photo_source": "https://commons.wikimedia.org/wiki/File:Praggnanandhaa_in_2025.jpg",
    },
    {
        "name": "Alireza Firouzja",
        "fide_id": "12573981",
        "dob": "06/18/2003",
        "photo": "firouzja.jpg",
        "photo_by": "Ahmed0112",
        "photo_license": "CC0",
        "photo_source": "https://commons.wikimedia.org/wiki/File:Alireza_Firouzja_2026_Norway_Chess.jpg",
    },
    {
        "name": "Jan-Krzysztof Duda",
        "fide_id": "1170546",
        "dob": "04/26/1998",
        "photo": "duda.jpg",
        "photo_by": "Danuta Matloch / Ministerstwo Kultury, Dziedzictwa Narodowego i Sportu",
        "photo_license": "CC BY 3.0 PL",
        "photo_source": "https://commons.wikimedia.org/wiki/File:Jan-Krzysztof_Duda_2021_(cropped).jpg",
    },
    {
        "name": "Viswanathan Anand",
        "fide_id": "5000017",
        "dob": "12/11/1969",
        "photo": "anand.jpg",
        "photo_by": "Wolfgang Jekel",
        "photo_license": "CC BY 2.0",
        "photo_source": "https://commons.wikimedia.org/wiki/File:Viswanathan_Anand_(2016)_(cropped).jpeg",
    },
    {
        "name": "Ding Liren",
        "fide_id": "8603677",
        "dob": "10/24/1992",
        "photo": "ding.jpg",
        "photo_by": "Stefan64",
        "photo_license": "CC BY-SA 3.0",
        "photo_source": "https://commons.wikimedia.org/wiki/File:DingLiren24a.jpg",
    },
]

# Top American-raised players — all grew up playing rated chess in the US, so
# their USCF histories are long enough to make interesting milestone charts.
# Added as USCF analyses.
FEATURED_USCF = [
    {
        "name": "Fabiano Caruana",
        "uscf_id": "12743305",
        "dob": "07/30/1992",
        "photo": "caruana.jpg",
        "photo_by": "Frans Peeters",
        "photo_license": "CC BY-SA 2.0",
        "photo_source": "https://commons.wikimedia.org/wiki/File:Fabiano_Caruana_in_2025.jpg",
    },
    {
        "name": "Hikaru Nakamura",
        "uscf_id": "12641216",
        "dob": "12/09/1987",
        "photo": "nakamura.jpg",
        "photo_by": "Andreas Kontokanis",
        "photo_license": "CC BY-SA 2.0",
        "photo_source": "https://commons.wikimedia.org/wiki/File:Nakamura_Hikaru_(29290269410)_(cropped).jpg",
    },
    {
        "name": "Hans Niemann",
        "uscf_id": "15041466",
        "dob": "06/20/2003",
        "photo": "niemann.jpg",
        "photo_by": "Frans Peeters",
        "photo_license": "CC BY-SA 2.0",
        "photo_source": "https://commons.wikimedia.org/wiki/File:Hans_Niemann_in_2024.jpg",
    },
    {
        "name": "Awonder Liang",
        "uscf_id": "13999045",
        "dob": "04/09/2003",
        "photo": "liang.jpg",
        "photo_by": "Chessherocanada",
        "photo_license": "CC BY-SA 4.0",
        "photo_source": "https://commons.wikimedia.org/wiki/File:Awonder_Liang.jpg",
    },
    {
        "name": "Samuel Sevian",
        "uscf_id": "13493815",
        "dob": "12/26/2000",
        "photo": "sevian.jpg",
        "photo_by": "Stefan64",
        "photo_license": "CC BY-SA 3.0",
        "photo_source": "https://commons.wikimedia.org/wiki/File:SamSevian23.jpg",
    },
    {
        "name": "Sam Shankland",
        "uscf_id": "12852765",
        "dob": "10/01/1991",
        "photo": "shankland.jpg",
        "photo_by": "Stefan64",
        "photo_license": "CC BY-SA 3.0",
        "photo_source": "https://commons.wikimedia.org/wiki/File:SamShankland23c.jpg",
    },
    {
        "name": "Ray Robson",
        "uscf_id": "12847250",
        "dob": "10/25/1994",
        "photo": "robson.jpg",
        "photo_by": "Stefan64",
        "photo_license": "CC BY-SA 3.0",
        "photo_source": "https://commons.wikimedia.org/wiki/File:RRobson10.jpg",
    },
    {
        "name": "Jeffery Xiong",
        "uscf_id": "13648621",
        "dob": "10/30/2000",
        "photo": "xiong.jpg",
        "photo_by": "Stefan64",
        "photo_license": "CC BY-SA 3.0",
        "photo_source": "https://commons.wikimedia.org/wiki/File:JefferyXiong23a.jpg",
    },
    {
        "name": "Abhimanyu Mishra",
        "uscf_id": "15456104",
        "dob": "02/05/2009",
        "photo": "mishra.jpg",
        "photo_by": "Frans Peeters",
        "photo_license": "CC BY-SA 2.0",
        "photo_source": "https://commons.wikimedia.org/wiki/File:Abhimanyu_Mishra_Tata_2023_-_72.jpg",
    },
    {
        "name": "Andy Woodward",
        "uscf_id": "16302012",
        "dob": "05/02/2010",
        "photo": "woodward.jpg",
        "photo_by": "Vysotsky",
        "photo_license": "CC BY-SA 4.0",
        "photo_source": "https://commons.wikimedia.org/wiki/File:TataSteelChess2026-11.jpg",
    },
]


def _photo_credits():
    """One attribution line per unique photo file (players shared between the
    two sections — same picture — are credited once)."""
    seen = {}
    for fp in FEATURED_FIDE + FEATURED_USCF:
        seen.setdefault(fp["photo"], fp)
    return list(seen.values())


PHOTO_CREDITS = _photo_credits()
