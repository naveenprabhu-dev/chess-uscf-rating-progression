"""Built-in "featured players" — top American-raised players offered as
one-click presets on the index page, so a user can populate the analyze form
without hunting down IDs and birthdates.

Every field here is public data:
  * ``uscf_id``  — verified against the player's live US Chess MSA member page
    (https://www.uschess.org/msa/MbrDtlMain.php?<id>); the displayed name matched.
  * ``dob``      — MM/DD/YYYY, from public sources (Wikipedia / FIDE / news).
  * photo fields — a Creative-Commons-licensed portrait stored under
    ``static/players/``, carrying the attribution the CC BY-SA licenses require.
    Full per-photo credit is also in ``static/players/CREDITS.md``.

Two of these players (Mishra, Woodward) are minors, but well-documented public
figures whose birthdates are central to their record-breaking GM coverage; only
those already-published dates are used. Nothing here is private personal data,
and the app only ever reads the same public USCF rating history it would for any
ID a user types in by hand.

USCF-only by design — the analyze form is USCF-only for now (FIDE is "coming
soon"), and all of these grew up playing rated chess in the US.
"""

FEATURED_PLAYERS = [
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
