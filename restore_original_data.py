import sqlite3
from datetime import date, timedelta

DB = "eventify.db"

CATEGORY_ICONS = {
    "Music": "fa-music",
    "Sports": "fa-futbol",
    "Technology": "fa-microchip",
    "Education": "fa-graduation-cap",
    "Business": "fa-briefcase",
    "Workshop": "fa-screwdriver-wrench",
    "Entertainment": "fa-masks-theater",
    "Cultural": "fa-drum",
    "College": "fa-user-graduate",
    "Food & Lifestyle": "fa-utensils",
}

EVENTS = [
    dict(
        title="Future Tech Conference 2026",
        category="Technology",
        location="Lucknow",
        venue="Lucknow Convention Centre",
        date=date.today() + timedelta(days=25),
        time="10:00 AM",
        organizer="TechSphere India",
        description="A two-day deep dive into AI, robotics, and the next wave of consumer technology, featuring keynotes from leading engineers and hands-on innovation labs.",
        image="https://images.unsplash.com/photo-1540575467063-178a50c2df87?w=900",
        total_seats=300,
        trending=True,
        tickets=[("Regular", 799), ("VIP", 1599), ("Premium", 2499)],
    ),
    dict(
        title="Music Night 2026",
        category="Music",
        location="Lucknow",
        venue="Ekana Open Grounds",
        date=date.today() + timedelta(days=12),
        time="7:00 PM",
        organizer="Soundwave Productions",
        description="An electric night of live performances spanning indie, EDM, and Bollywood fusion acts under the open sky.",
        image="https://images.unsplash.com/photo-1470229722913-7c0e2dbbafd3?w=900",
        total_seats=500,
        trending=True,
        tickets=[("Regular", 999), ("VIP", 1999), ("Premium", 2999)],
    ),
    dict(
        title="Creative Design Workshop",
        category="Workshop",
        location="Delhi",
        venue="Design Hub Studio",
        date=date.today() + timedelta(days=18),
        time="11:00 AM",
        organizer="Studio Canvas",
        description="A hands-on workshop covering UI/UX fundamentals, prototyping, and portfolio building led by senior product designers.",
        image="https://images.unsplash.com/photo-1531482615713-2afd69097998?w=900",
        total_seats=80,
        trending=False,
        tickets=[("Regular", 499), ("Premium", 899)],
    ),
    dict(
        title="Inter-College Sports Fest",
        category="Sports",
        location="Lucknow",
        venue="University Sports Complex",
        date=date.today() + timedelta(days=9),
        time="8:00 AM",
        organizer="Campus Sports League",
        description="Athletes from across the region compete in track, football, basketball and kabaddi finals over a thrilling weekend.",
        image="https://images.unsplash.com/photo-1461896836934-ffe607ba8211?w=900",
        total_seats=600,
        trending=True,
        tickets=[("Regular", 299), ("VIP", 599)],
    ),
    dict(
        title="Startup & Business Summit",
        category="Business",
        location="Mumbai",
        venue="BKC Grand Hall",
        date=date.today() + timedelta(days=35),
        time="9:30 AM",
        organizer="VentureNext",
        description="Founders, investors and operators gather for panels on fundraising, scaling, and building category-defining startups.",
        image="https://images.unsplash.com/photo-1515187029135-18ee286d815b?w=900",
        total_seats=250,
        trending=False,
        tickets=[("Regular", 1199), ("VIP", 2199), ("Premium", 3499)],
    ),
    dict(
        title="Cultural Fest 2026",
        category="Cultural",
        location="Lucknow",
        venue="Nawabi Heritage Grounds",
        date=date.today() + timedelta(days=15),
        time="5:00 PM",
        organizer="Awadh Cultural Society",
        description="Celebrate classical dance, folk music and regional cuisine in a vibrant showcase of Awadh heritage and culture.",
        image="https://images.unsplash.com/photo-1533174072545-7a4b6ad7a6c3?w=900",
        total_seats=400,
        trending=False,
        tickets=[("Regular", 399), ("VIP", 799)],
    ),
    dict(
        title="AI & Robotics Expo",
        category="Technology",
        location="Bengaluru",
        venue="Palace Grounds Expo Centre",
        date=date.today() + timedelta(days=42),
        time="10:00 AM",
        organizer="RoboFuture Labs",
        description="Explore cutting-edge robotics demos, AI research showcases and live hackathon finals from top engineering teams.",
        image="https://images.unsplash.com/photo-1485827404703-89b55fcc595e?w=900",
        total_seats=350,
        trending=True,
        tickets=[("Regular", 899), ("VIP", 1799)],
    ),
    dict(
        title="Stand-Up Comedy Night",
        category="Entertainment",
        location="Delhi",
        venue="Laugh Lounge",
        date=date.today() + timedelta(days=7),
        time="8:30 PM",
        organizer="Chuckle Club",
        description="An evening of sharp, unfiltered stand-up from some of the country's sharpest emerging comedians.",
        image="https://images.unsplash.com/photo-1585699324551-f6c309eedeca?w=900",
        total_seats=150,
        trending=False,
        tickets=[("Regular", 349), ("VIP", 699)],
    ),
    dict(
        title="Food & Wine Carnival",
        category="Food & Lifestyle",
        location="Goa",
        venue="Candolim Beachfront",
        date=date.today() + timedelta(days=20),
        time="4:00 PM",
        organizer="Palate Collective",
        description="A beachside carnival of gourmet food stalls, craft beverages and live acoustic sets as the sun goes down.",
        image="https://images.unsplash.com/photo-1414235077428-338989a2e8c0?w=900",
        total_seats=220,
        trending=False,
        tickets=[("Regular", 599), ("Premium", 1099)],
    ),
    dict(
        title="Campus Freshers Carnival",
        category="College",
        location="Lucknow",
        venue="IET Campus Grounds",
        date=date.today() + timedelta(days=5),
        time="6:00 PM",
        organizer="Student Council",
        description="Welcome the new batch with games, live music, food trucks and a DJ night to close out the celebrations.",
        image="https://images.unsplash.com/photo-1523580494863-6f3031224c94?w=900",
        total_seats=450,
        trending=True,
        tickets=[("Regular", 199), ("VIP", 399)],
    ),
    dict(
        title="Data Science Bootcamp",
        category="Education",
        location="Pune",
        venue="Innovation Learning Centre",
        date=date.today() + timedelta(days=28),
        time="9:00 AM",
        organizer="LearnForge Academy",
        description="An intensive full-day bootcamp covering Python, machine learning foundations, and real-world case studies.",
        image="https://images.unsplash.com/photo-1551288049-bebda4e38f71?w=900",
        total_seats=120,
        trending=False,
        tickets=[("Regular", 699), ("Premium", 1299)],
    ),
    dict(
        title="Indie Film Screening Weekend",
        category="Entertainment",
        location="Chennai",
        venue="Marina Arthouse Cinema",
        date=date.today() + timedelta(days=14),
        time="3:00 PM",
        organizer="Frame by Frame Collective",
        description="A curated weekend of independent short films and features, followed by director Q&A sessions.",
        image="https://images.unsplash.com/photo-1489599162946-99ba3b7dcf8e?w=900",
        total_seats=180,
        trending=False,
        tickets=[("Regular", 299), ("VIP", 549)],
    ),
]


def main():
    conn = sqlite3.connect(DB)
    conn.execute("PRAGMA foreign_keys = ON")
    cur = conn.cursor()

    # Make sure the Phase 9 schema exists.
    # This does NOT delete anything.
    required_tables = [
        "categories",
        "events",
        "ticket_types",
    ]

    for table in required_tables:
        if not cur.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (table,),
        ).fetchone():
            raise RuntimeError(
                f"Required table '{table}' does not exist. "
                "Start the Phase 9 app once first so run_migrations() can create the schema."
            )

    # Categories
    category_ids = {}

    for name, icon in CATEGORY_ICONS.items():
        row = cur.execute(
            "SELECT id FROM categories WHERE name=?",
            (name,),
        ).fetchone()

        if row:
            category_ids[name] = row[0]
        else:
            cur.execute(
                "INSERT INTO categories (name, icon) VALUES (?, ?)",
                (name, icon),
            )
            category_ids[name] = cur.lastrowid

    inserted_events = 0
    inserted_tickets = 0

    for ev in EVENTS:
        # Never duplicate an event with the same title.
        existing = cur.execute(
            "SELECT id FROM events WHERE title=?",
            (ev["title"],),
        ).fetchone()

        if existing:
            event_id = existing[0]
            print(f"SKIP existing event: {ev['title']}")
            continue

        cur.execute(
            """
            INSERT INTO events (
                title,
                description,
                category_id,
                location,
                venue,
                date,
                time,
                organizer,
                image,
                total_seats,
                booked_seats,
                is_trending
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                ev["title"],
                ev["description"],
                category_ids[ev["category"]],
                ev["location"],
                ev["venue"],
                ev["date"].isoformat(),
                ev["time"],
                ev["organizer"],
                ev["image"],
                ev["total_seats"],
                int(ev["total_seats"] * 0.35),
                1 if ev["trending"] else 0,
            ),
        )

        event_id = cur.lastrowid
        inserted_events += 1

        for ticket_name, price in ev["tickets"]:
            cur.execute(
                """
                INSERT INTO ticket_types (
                    event_id,
                    name,
                    price,
                    is_streaming
                )
                VALUES (?, ?, ?, ?)
                """,
                (
                    event_id,
                    ticket_name,
                    price,
                    0,
                ),
            )
            inserted_tickets += 1

        print(f"INSERTED: {ev['title']}")

    conn.commit()

    print()
    print("========================================")
    print("EVENTIFY DATA RESTORATION COMPLETE")
    print("========================================")
    print("Categories:", cur.execute("SELECT COUNT(*) FROM categories").fetchone()[0])
    print("Events:", cur.execute("SELECT COUNT(*) FROM events").fetchone()[0])
    print("Ticket types:", cur.execute("SELECT COUNT(*) FROM ticket_types").fetchone()[0])
    print("Users:", cur.execute("SELECT COUNT(*) FROM users").fetchone()[0])
    print("New events inserted:", inserted_events)
    print("New tickets inserted:", inserted_tickets)

    conn.close()


if __name__ == "__main__":
    main()