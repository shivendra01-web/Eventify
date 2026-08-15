"""Run this once to create the database and populate it with sample data.
Usage: python seed.py
"""
from datetime import date, timedelta
from app import app
from models import db, User, Category, Event, TicketType

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
    dict(title="Future Tech Conference 2026", category="Technology", location="Lucknow",
         venue="Lucknow Convention Centre", date=date.today() + timedelta(days=25), time="10:00 AM",
         organizer="TechSphere India",
         description="A two-day deep dive into AI, robotics, and the next wave of consumer technology, "
                      "featuring keynotes from leading engineers and hands-on innovation labs.",
         image="https://images.unsplash.com/photo-1540575467063-178a50c2df87?w=900",
         total_seats=300, trending=True, tickets=[("Regular", 799), ("VIP", 1599), ("Premium", 2499)]),
    dict(title="Music Night 2026", category="Music", location="Lucknow",
         venue="Ekana Open Grounds", date=date.today() + timedelta(days=12), time="7:00 PM",
         organizer="Soundwave Productions",
         description="An electric night of live performances spanning indie, EDM, and Bollywood fusion "
                      "acts under the open sky.",
         image="https://images.unsplash.com/photo-1470229722913-7c0e2dbbafd3?w=900",
         total_seats=500, trending=True, tickets=[("Regular", 999), ("VIP", 1999), ("Premium", 2999)]),
    dict(title="Creative Design Workshop", category="Workshop", location="Delhi",
         venue="Design Hub Studio", date=date.today() + timedelta(days=18), time="11:00 AM",
         organizer="Studio Canvas",
         description="A hands-on workshop covering UI/UX fundamentals, prototyping, and portfolio "
                      "building led by senior product designers.",
         image="https://images.unsplash.com/photo-1531482615713-2afd69097998?w=900",
         total_seats=80, trending=False, tickets=[("Regular", 499), ("Premium", 899)]),
    dict(title="Inter-College Sports Fest", category="Sports", location="Lucknow",
         venue="University Sports Complex", date=date.today() + timedelta(days=9), time="8:00 AM",
         organizer="Campus Sports League",
         description="Athletes from across the region compete in track, football, basketball and "
                      "kabaddi finals over a thrilling weekend.",
         image="https://images.unsplash.com/photo-1461896836934-ffe607ba8211?w=900",
         total_seats=600, trending=True, tickets=[("Regular", 299), ("VIP", 599)]),
    dict(title="Startup & Business Summit", category="Business", location="Mumbai",
         venue="BKC Grand Hall", date=date.today() + timedelta(days=35), time="9:30 AM",
         organizer="VentureNext",
         description="Founders, investors and operators gather for panels on fundraising, scaling, "
                      "and building category-defining startups.",
         image="https://images.unsplash.com/photo-1515187029135-18ee286d815b?w=900",
         total_seats=250, trending=False, tickets=[("Regular", 1199), ("VIP", 2199), ("Premium", 3499)]),
    dict(title="Cultural Fest 2026", category="Cultural", location="Lucknow",
         venue="Nawabi Heritage Grounds", date=date.today() + timedelta(days=15), time="5:00 PM",
         organizer="Awadh Cultural Society",
         description="Celebrate classical dance, folk music and regional cuisine in a vibrant "
                      "showcase of Awadh heritage and culture.",
         image="https://images.unsplash.com/photo-1533174072545-7a4b6ad7a6c3?w=900",
         total_seats=400, trending=False, tickets=[("Regular", 399), ("VIP", 799)]),
    dict(title="AI & Robotics Expo", category="Technology", location="Bengaluru",
         venue="Palace Grounds Expo Centre", date=date.today() + timedelta(days=42), time="10:00 AM",
         organizer="RoboFuture Labs",
         description="Explore cutting-edge robotics demos, AI research showcases and live "
                      "hackathon finals from top engineering teams.",
         image="https://images.unsplash.com/photo-1485827404703-89b55fcc595e?w=900",
         total_seats=350, trending=True, tickets=[("Regular", 899), ("VIP", 1799)]),
    dict(title="Stand-Up Comedy Night", category="Entertainment", location="Delhi",
         venue="Laugh Lounge", date=date.today() + timedelta(days=7), time="8:30 PM",
         organizer="Chuckle Club",
         description="An evening of sharp, unfiltered stand-up from some of the country's "
                      "sharpest emerging comedians.",
         image="https://images.unsplash.com/photo-1585699324551-f6c309eedeca?w=900",
         total_seats=150, trending=False, tickets=[("Regular", 349), ("VIP", 699)]),
    dict(title="Food & Wine Carnival", category="Food & Lifestyle", location="Goa",
         venue="Candolim Beachfront", date=date.today() + timedelta(days=20), time="4:00 PM",
         organizer="Palate Collective",
         description="A beachside carnival of gourmet food stalls, craft beverages and live "
                      "acoustic sets as the sun goes down.",
         image="https://images.unsplash.com/photo-1414235077428-338989a2e8c0?w=900",
         total_seats=220, trending=False, tickets=[("Regular", 599), ("Premium", 1099)]),
    dict(title="Campus Freshers Carnival", category="College", location="Lucknow",
         venue="IET Campus Grounds", date=date.today() + timedelta(days=5), time="6:00 PM",
         organizer="Student Council",
         description="Welcome the new batch with games, live music, food trucks and a DJ "
                      "night to close out the celebrations.",
         image="https://images.unsplash.com/photo-1523580494863-6f3031224c94?w=900",
         total_seats=450, trending=True, tickets=[("Regular", 199), ("VIP", 399)]),
    dict(title="Data Science Bootcamp", category="Education", location="Pune",
         venue="Innovation Learning Centre", date=date.today() + timedelta(days=28), time="9:00 AM",
         organizer="LearnForge Academy",
         description="An intensive full-day bootcamp covering Python, machine learning "
                      "foundations, and real-world case studies.",
         image="https://images.unsplash.com/photo-1551288049-bebda4e38f71?w=900",
         total_seats=120, trending=False, tickets=[("Regular", 699), ("Premium", 1299)]),
    dict(title="Indie Film Screening Weekend", category="Entertainment", location="Chennai",
         venue="Marina Arthouse Cinema", date=date.today() + timedelta(days=14), time="3:00 PM",
         organizer="Frame by Frame Collective",
         description="A curated weekend of independent short films and features, followed by "
                      "director Q&A sessions.",
         image="https://images.unsplash.com/photo-1489599162946-99ba3b7dcf8e?w=900",
         total_seats=180, trending=False, tickets=[("Regular", 299), ("VIP", 549)]),
]


def run():
    with app.app_context():
        db.drop_all()
        db.create_all()

        cat_objs = {}
        for name, icon in CATEGORY_ICONS.items():
            c = Category(name=name, icon=icon)
            db.session.add(c)
            cat_objs[name] = c
        db.session.flush()

        for ev in EVENTS:
            event = Event(
                title=ev["title"], description=ev["description"],
                category_id=cat_objs[ev["category"]].id, location=ev["location"],
                venue=ev["venue"], date=ev["date"], time=ev["time"],
                organizer=ev["organizer"], image=ev["image"],
                total_seats=ev["total_seats"], booked_seats=int(ev["total_seats"] * 0.35),
                is_trending=ev["trending"],
            )
            db.session.add(event)
            db.session.flush()
            for name, price in ev["tickets"]:
                db.session.add(TicketType(event_id=event.id, name=name, price=price))

        admin = User(name="Admin", email="admin@eventify.com", phone="9999999999", role="admin")
        admin.set_password("admin123")
        db.session.add(admin)

        demo = User(name="Aarav Sharma", email="demo@eventify.com", phone="9876543210", role="user")
        demo.set_password("demo1234")
        db.session.add(demo)

        db.session.commit()
        print("Database seeded successfully.")
        print("Admin login  -> admin@eventify.com / admin123")
        print("Demo user    -> demo@eventify.com / demo1234")


if __name__ == "__main__":
    run()
