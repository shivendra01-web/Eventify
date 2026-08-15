"""Stage 2 — ADDITIVE event seeder.

Unlike seed.py (which drops and recreates everything), this script only ADDS
new categories/events/ticket-types. It checks for existing events by title
before inserting, so it is safe to run more than once and will NEVER delete,
reset, or duplicate your existing users, bookings, payments, or events.

Usage: python seed_stage2.py
"""
from datetime import date, timedelta
from app import app
from models import db, Category, Event, TicketType

NEW_CATEGORY_ICONS = {
    "Comedy": "fa-face-laugh-beam",
    "Startup": "fa-rocket",
    "Art": "fa-palette",
    "Food Festival": "fa-utensils",
    "Gaming": "fa-gamepad",
    "Freshers Celebration": "fa-graduation-cap",
    "Community Program": "fa-people-group",
}

# All events below (except ULLAS) are priced strictly below ₹1,000 per the brief.
EVENTS = [
    # ---------- Featured: ULLAS ----------
    dict(title="ULLAS", category="Cultural", location="Lucknow",
         venue="SRM University, Lucknow", date=date.today() + timedelta(days=40), time="6:00 PM",
         organizer="SRM University Lucknow",
         description="Eventify's flagship college fest featuring performances, competitions and a "
                      "celebrity appearance. Chief Guest: Virat Kohli (event data supplied for this "
                      "demo — not independently verified or officially confirmed).",
         image="https://images.unsplash.com/photo-1508997449629-303059a039c0?w=900",
         total_seats=50, trending=True, tickets=[("VVIP Meet & Greet", 1000000)]),

    # ---------- Lucknow ----------
    dict(title="Lucknow Battle of Bands", category="Music", location="Lucknow",
         venue="Ekana Sports City Grounds", date=date.today() + timedelta(days=10), time="6:30 PM",
         organizer="Nawabi Sound Collective",
         description="Six college bands compete for the title in an evening of rock, indie and fusion sets.",
         image="https://images.unsplash.com/photo-1501281668745-f7f57925c3b4?w=900",
         total_seats=400, trending=True, tickets=[("Regular", 249), ("Premium", 599)]),
    dict(title="Awadh Heritage Walk & Fest", category="Cultural", location="Lucknow",
         venue="Bara Imambara Grounds", date=date.today() + timedelta(days=16), time="4:00 PM",
         organizer="Lucknow Heritage Trust",
         description="A guided heritage walk through Old Lucknow followed by a cultural showcase of "
                      "Awadhi music, food and craft stalls.",
         image="https://images.unsplash.com/photo-1524492412937-b28074a5d7da?w=900",
         total_seats=250, trending=False, tickets=[("Regular", 299)]),
    dict(title="Lucknow Coders Meetup", category="Technology", location="Lucknow",
         venue="IET Lucknow Auditorium", date=date.today() + timedelta(days=8), time="10:00 AM",
         organizer="DevCircle Lucknow",
         description="A community meetup with lightning talks on web dev, AI tooling and open source, "
                      "followed by networking over snacks.",
         image="https://images.unsplash.com/photo-1515187029135-18ee286d815b?w=900",
         total_seats=150, trending=False, tickets=[("Regular", 149), ("Premium", 349)]),

    # ---------- Mumbai ----------
    dict(title="Mumbai Open Mic Comedy Night", category="Comedy", location="Mumbai",
         venue="The Habitat, Khar", date=date.today() + timedelta(days=6), time="8:00 PM",
         organizer="Laugh Riot Productions",
         description="Ten comics, five minutes each, one unpredictable night of stand-up in the "
                      "heart of Mumbai's comedy scene.",
         image="https://images.unsplash.com/photo-1585699324551-f6c309eedeca?w=900",
         total_seats=120, trending=True, tickets=[("Regular", 349), ("Premium", 699)]),
    dict(title="Mumbai Skyline Music Fest", category="Music", location="Mumbai",
         venue="Mahalaxmi Racecourse Lawns", date=date.today() + timedelta(days=22), time="5:00 PM",
         organizer="Bayline Live",
         description="An open-air evening of indie and electronic acts with the Mumbai skyline as a backdrop.",
         image="https://images.unsplash.com/photo-1470229722913-7c0e2dbbafd3?w=900",
         total_seats=600, trending=True, tickets=[("Regular", 599), ("Premium", 999)]),
    dict(title="Mumbai UX Design Workshop", category="Workshop", location="Mumbai",
         venue="WeWork BKC", date=date.today() + timedelta(days=19), time="10:30 AM",
         organizer="Pixel Practice Studio",
         description="A full-day hands-on workshop covering research, wireframing and prototyping "
                      "for early-career product designers.",
         image="https://images.unsplash.com/photo-1531482615713-2afd69097998?w=900",
         total_seats=70, trending=False, tickets=[("Regular", 499)]),

    # ---------- Bengaluru ----------
    dict(title="Bengaluru DevOps Conclave", category="Technology", location="Bengaluru",
         venue="NIMHANS Convention Centre", date=date.today() + timedelta(days=30), time="9:30 AM",
         organizer="CloudNative Bengaluru",
         description="Talks and panels on Kubernetes, CI/CD and platform engineering from practitioners "
                      "at India's fastest-growing product companies.",
         image="https://images.unsplash.com/photo-1591453089816-0fbb971b454c?w=900",
         total_seats=300, trending=True, tickets=[("Regular", 799), ("Premium", 950)]),
    dict(title="Bengaluru Startup Pitch Night", category="Startup", location="Bengaluru",
         venue="91springboard Koramangala", date=date.today() + timedelta(days=13), time="6:00 PM",
         organizer="Founders' Circle",
         description="Early-stage founders pitch to a room of angel investors and fellow builders, "
                      "followed by open networking.",
         image="https://images.unsplash.com/photo-1552664730-d307ca884978?w=900",
         total_seats=180, trending=False, tickets=[("Regular", 249)]),
    dict(title="Bengaluru LAN Gaming Arena", category="Gaming", location="Bengaluru",
         venue="Phoenix MarketCity Esports Zone", date=date.today() + timedelta(days=11), time="11:00 AM",
         organizer="LevelUp Esports",
         description="A day-long LAN tournament across Valorant, BGMI and FIFA with live commentary "
                      "and a spectator zone.",
         image="https://images.unsplash.com/photo-1542751371-adc38448a05e?w=900",
         total_seats=200, trending=True, tickets=[("Regular", 199), ("Premium", 399)]),

    # ---------- Chennai ----------
    dict(title="Chennai Classical Confluence", category="Cultural", location="Chennai",
         venue="Music Academy Auditorium", date=date.today() + timedelta(days=27), time="5:30 PM",
         organizer="Carnatic Circle Chennai",
         description="An evening of Carnatic vocal and instrumental performances by rising young artists.",
         image="https://images.unsplash.com/photo-1465847899084-d164df4dedc6?w=900",
         total_seats=350, trending=False, tickets=[("Regular", 349), ("Premium", 649)]),
    dict(title="Chennai Product Design Sprint", category="Workshop", location="Chennai",
         venue="Tidel Park Innovation Hub", date=date.today() + timedelta(days=17), time="9:00 AM",
         organizer="Design Sprint Collective",
         description="A compact two-day sprint teaching rapid prototyping and user-testing techniques.",
         image="https://images.unsplash.com/photo-1522071820081-009f0129c71c?w=900",
         total_seats=90, trending=False, tickets=[("Regular", 549)]),

    # ---------- Noida ----------
    dict(title="Noida Tech Career Fair", category="Technology", location="Noida",
         venue="India Expo Mart", date=date.today() + timedelta(days=21), time="10:00 AM",
         organizer="HireForward",
         description="Meet recruiters from leading tech companies, attend resume clinics and sit in "
                      "on rapid-fire skills workshops.",
         image="https://images.unsplash.com/photo-1519389950473-47ba0277781c?w=900",
         total_seats=400, trending=False, tickets=[("Regular", 99)]),
    dict(title="Noida Startup Founders Brunch", category="Startup", location="Noida",
         venue="Ffresh Bistro, Sector 18", date=date.today() + timedelta(days=9), time="11:00 AM",
         organizer="Founders' Circle Noida",
         description="An informal brunch meetup for early founders to swap notes on fundraising and hiring.",
         image="https://images.unsplash.com/photo-1556761175-5973dc0f32e7?w=900",
         total_seats=60, trending=False, tickets=[("Regular", 299)]),
    dict(title="Noida College Comedy Jam", category="Comedy", location="Noida",
         venue="Amity University Auditorium", date=date.today() + timedelta(days=7), time="7:00 PM",
         organizer="Campus Laughs Collective",
         description="Campus comedians and one touring headliner share the stage for a college-crowd "
                      "comedy night.",
         image="https://images.unsplash.com/photo-1543584756-403b982622f0?w=900",
         total_seats=220, trending=False, tickets=[("Regular", 149)]),

    # ---------- Delhi ----------
    dict(title="Delhi Art & Design Bazaar", category="Art", location="Delhi",
         venue="Dilli Haat", date=date.today() + timedelta(days=24), time="12:00 PM",
         organizer="Craft Collective Delhi",
         description="An open-air showcase of independent artists, illustrators and designers selling "
                      "original work alongside live mural sessions.",
         image="https://images.unsplash.com/photo-1460661419201-fd4cecdf8a8b?w=900",
         total_seats=300, trending=False, tickets=[("Regular", 99)]),
    dict(title="Delhi Winter Food Carnival", category="Food Festival", location="Delhi",
         venue="Jawaharlal Nehru Stadium Grounds", date=date.today() + timedelta(days=33), time="1:00 PM",
         organizer="Capital Bites Collective",
         description="Over eighty stalls of street food, regional cuisine and dessert pop-ups across "
                      "one long winter afternoon.",
         image="https://images.unsplash.com/photo-1555939594-58d7cb561ad1?w=900",
         total_seats=800, trending=True, tickets=[("Regular", 149), ("Premium", 399)]),

    # ---------- Pune ----------
    dict(title="Pune Indie Music Circuit", category="Music", location="Pune",
         venue="High Spirits Lawns", date=date.today() + timedelta(days=15), time="7:00 PM",
         organizer="Deccan Sound Collective",
         description="Four up-and-coming indie acts share the stage on Pune's most reliable live-music night.",
         image="https://images.unsplash.com/photo-1459749411175-04bf5292ceea?w=900",
         total_seats=280, trending=False, tickets=[("Regular", 249)]),
    dict(title="Pune College Fest — Spandan", category="College Fest", location="Pune",
         venue="COEP Technological University", date=date.today() + timedelta(days=19), time="10:00 AM",
         organizer="COEP Student Council",
         description="Three days of competitions, workshops and evening concerts across Pune's largest "
                      "college fest.",
         image="https://images.unsplash.com/photo-1523580494863-6f3031224c94?w=900",
         total_seats=500, trending=True, tickets=[("Regular", 199), ("Premium", 449)]),

    # ---------- Hyderabad ----------
    dict(title="Hyderabad Cloud & AI Summit", category="Technology", location="Hyderabad",
         venue="HITEC City Convention Centre", date=date.today() + timedelta(days=36), time="9:00 AM",
         organizer="TelanganaTech Forum",
         description="Enterprise-focused sessions on applied AI, cloud cost optimization and platform "
                      "reliability from regional tech leaders.",
         image="https://images.unsplash.com/photo-1485827404703-89b55fcc595e?w=900",
         total_seats=350, trending=False, tickets=[("Regular", 699), ("Premium", 899)]),
    dict(title="Hyderabad Biryani & Food Fest", category="Food Festival", location="Hyderabad",
         venue="People's Plaza, Necklace Road", date=date.today() + timedelta(days=12), time="12:30 PM",
         organizer="Nizami Flavours Collective",
         description="A celebration of Hyderabadi cuisine featuring dozens of biryani stalls and live "
                      "cooking demonstrations.",
         image="https://images.unsplash.com/photo-1563379091339-03246963d96c?w=900",
         total_seats=450, trending=True, tickets=[("Regular", 199), ("Premium", 449)]),

    # ---------- Jaipur ----------
    dict(title="Jaipur Literature & Art Meet", category="Art", location="Jaipur",
         venue="Jawahar Kala Kendra", date=date.today() + timedelta(days=29), time="11:00 AM",
         organizer="Pink City Arts Council",
         description="Author readings, panel discussions and a curated exhibition of contemporary "
                      "Rajasthani art.",
         image="https://images.unsplash.com/photo-1513364776144-60967b0f800f?w=900",
         total_seats=200, trending=False, tickets=[("Regular", 149)]),

    # ---------- Kolkata ----------
    dict(title="Kolkata Jazz & Blues Evening", category="Music", location="Kolkata",
         venue="Someplace Else, Park Street", date=date.today() + timedelta(days=14), time="8:00 PM",
         organizer="Park Street Live",
         description="An intimate evening of jazz standards and blues improvisation from Kolkata's "
                      "resident house band and guest musicians.",
         image="https://images.unsplash.com/photo-1511192336575-5a79af67a629?w=900",
         total_seats=140, trending=False, tickets=[("Regular", 349), ("Premium", 599)]),
    dict(title="Kolkata Gaming Championship", category="Gaming", location="Kolkata",
         venue="Nicco Park Esports Arena", date=date.today() + timedelta(days=18), time="10:00 AM",
         organizer="EastZone Esports",
         description="Regional qualifiers for BGMI and FIFA with cash prizes and a full spectator setup.",
         image="https://images.unsplash.com/photo-1493711662062-fa541adb3fc8?w=900",
         total_seats=250, trending=False, tickets=[("Regular", 149), ("Premium", 299)]),

    # ---------- Special demo-priced events ----------
    dict(title="Anubhuti 2027", category="Freshers Celebration", location="Lucknow",
         venue="SRM University Lucknow", date=date.today() + timedelta(days=60), time="6:00 PM",
         organizer="SRM University Lucknow",
         description="A freshers celebration event at SRM University Lucknow. Chief Guest: Mr. Sundar "
                      "Pichai, CEO of Google (event data supplied for this demo — not independently "
                      "verified or officially confirmed). Date shown is a placeholder demo date, not a "
                      "confirmed schedule.",
         image="https://images.unsplash.com/photo-1523580494863-6f3031224c94?w=900",
         total_seats=500, trending=True, tickets=[("Regular", 11)]),
    dict(title="Youth Development Program", category="Community Program", location="Lucknow",
         venue="Ekana Stadium, Lucknow", date=date.today() + timedelta(days=50), time="10:00 AM",
         organizer="Eventify Community Events",
         description="A youth development program in Lucknow. Chief Guests: Mr. Rakesh Prakash Singh, "
                      "IPS, Inspector General of Police, Lucknow, and Mr. Radhe Shyam Singh, Sub "
                      "Divisional Magistrate (SDM), Lucknow (event data supplied for this demo — not "
                      "independently verified or officially confirmed). Date shown is a placeholder "
                      "demo date, not a confirmed schedule.",
         image="https://images.unsplash.com/photo-1517457373958-b7bdd4587205?w=900",
         total_seats=600, trending=False, tickets=[("Regular", 1999)]),
]


def run():
    with app.app_context():
        # Ensure any brand-new categories exist (additive get-or-create; never touches existing ones)
        existing_categories = {c.name: c for c in Category.query.all()}
        for name, icon in NEW_CATEGORY_ICONS.items():
            if name not in existing_categories:
                cat = Category(name=name, icon=icon)
                db.session.add(cat)
                existing_categories[name] = cat
        db.session.flush()

        added = 0
        skipped = 0
        for ev in EVENTS:
            if Event.query.filter_by(title=ev["title"]).first():
                skipped += 1
                continue  # already seeded — never duplicate or overwrite

            category = existing_categories.get(ev["category"])
            if category is None:
                # Category should already exist from seed.py or NEW_CATEGORY_ICONS above
                category = Category(name=ev["category"], icon="fa-star")
                db.session.add(category)
                db.session.flush()
                existing_categories[ev["category"]] = category

            event = Event(
                title=ev["title"], description=ev["description"],
                category_id=category.id, location=ev["location"],
                venue=ev["venue"], date=ev["date"], time=ev["time"],
                organizer=ev["organizer"], image=ev["image"],
                total_seats=ev["total_seats"],
                booked_seats=int(ev["total_seats"] * 0.2),
                is_trending=ev["trending"],
            )
            db.session.add(event)
            db.session.flush()
            for name, price in ev["tickets"]:
                db.session.add(TicketType(event_id=event.id, name=name, price=price))
            added += 1

        db.session.commit()
        print(f"Stage 2 event seeding complete. Added {added} new event(s), skipped {skipped} already present.")


if __name__ == "__main__":
    run()
