import os
import io
import re
import base64
import random
import string
import secrets
from datetime import datetime, date, timedelta, timezone
from functools import wraps

from dotenv import load_dotenv
from flask import (Flask, render_template, redirect, url_for, request,
                    flash, jsonify, abort, session)
from flask_login import (LoginManager, login_user, logout_user, login_required,
                          current_user)
import qrcode
import razorpay

from models import (db, User, Category, Event, TicketType, Booking, BookingItem,
                     Wishlist, Payment, Coupon, TicketTransfer, Interest, Follow,
                     EventReaction, EventComment, CommentLike, CommentReport, REACTION_TYPES,
                     Review, ReviewHelpful, ReviewReport,
                     Notification, notify, Badge, UserBadge, PointsTransaction, award_points,
                     Referral, EventGroup, EventGroupMember, GroupInvitation, Poll, PollOption, PollVote,
                     SayHi, Block)

# Loads variables from a local .env file if present (for local development).
# On Render (or any host where the vars are set in the environment already),
# this simply finds no .env file and does nothing — it never overrides
# real environment variables that are already set.
load_dotenv()

BASE_DIR = os.path.abspath(os.path.dirname(__file__))

app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "eventify-local-dev-secret-key")
app.config["SQLALCHEMY_DATABASE_URI"] = f"sqlite:///{os.path.join(BASE_DIR, 'eventify.db')}"
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

# ---------- Razorpay configuration (Test Mode credentials, via env vars) ----------
RAZORPAY_KEY_ID = os.environ.get("RAZORPAY_KEY_ID")
RAZORPAY_KEY_SECRET = os.environ.get("RAZORPAY_KEY_SECRET")


def get_razorpay_client():
    """Returns a configured Razorpay client, or None if credentials aren't set.
    The secret key never leaves this server-side function."""
    if not RAZORPAY_KEY_ID or not RAZORPAY_KEY_SECRET:
        return None
    return razorpay.Client(auth=(RAZORPAY_KEY_ID, RAZORPAY_KEY_SECRET))


db.init_app(app)


def run_migrations():
    """Non-destructive startup migration. Only ADDs missing columns/tables —
    never drops or resets anything, so existing bookings/users/events are preserved."""
    with app.app_context():
        db.create_all()  # only creates tables that don't already exist (e.g. new "coupons" table)
        inspector = db.inspect(db.engine)

        if "payments" in inspector.get_table_names():
            existing_cols = {c["name"] for c in inspector.get_columns("payments")}
            new_cols = {
                "razorpay_order_id": "VARCHAR(80)",
                "razorpay_payment_id": "VARCHAR(80)",
                "razorpay_signature": "VARCHAR(255)",
            }
            with db.engine.begin() as conn:
                for col_name, col_type in new_cols.items():
                    if col_name not in existing_cols:
                        conn.execute(db.text(f"ALTER TABLE payments ADD COLUMN {col_name} {col_type}"))

        if "bookings" in inspector.get_table_names():
            existing_cols = {c["name"] for c in inspector.get_columns("bookings")}
            new_cols = {
                "coupon_code": "VARCHAR(40)",
                "discount_amount": "FLOAT",
                "meal_choice": "VARCHAR(40)",
            }
            with db.engine.begin() as conn:
                for col_name, col_type in new_cols.items():
                    if col_name not in existing_cols:
                        conn.execute(db.text(f"ALTER TABLE bookings ADD COLUMN {col_name} {col_type}"))

        # ---------- Phase 9: admin coupon management — additive columns ----------
        if "coupons" in inspector.get_table_names():
            existing_cols = {c["name"] for c in inspector.get_columns("coupons")}
            new_cols = {
                "discount_type": "VARCHAR(10) DEFAULT 'percent'",
                "discount_amount": "FLOAT",
                "min_booking_amount": "FLOAT",
                "max_discount_amount": "FLOAT",
                "usage_limit": "INTEGER",
                "times_used": "INTEGER DEFAULT 0",
                "expiry_date": "DATE",
            }
            with db.engine.begin() as conn:
                for col_name, col_type in new_cols.items():
                    if col_name not in existing_cols:
                        conn.execute(db.text(f"ALTER TABLE coupons ADD COLUMN {col_name} {col_type}"))

        # Seed the Shivendra50 coupon if it doesn't already exist (idempotent — safe to run every startup)
        if not Coupon.query.filter_by(code="Shivendra50").first():
            db.session.add(Coupon(code="Shivendra50", discount_percent=50, active=True))
            db.session.commit()

        if "ticket_types" in inspector.get_table_names():
            existing_cols = {c["name"] for c in inspector.get_columns("ticket_types")}
            if "is_streaming" not in existing_cols:
                with db.engine.begin() as conn:
                    conn.execute(db.text("ALTER TABLE ticket_types ADD COLUMN is_streaming BOOLEAN DEFAULT 0"))

        if "events" in inspector.get_table_names():
            existing_cols = {c["name"] for c in inspector.get_columns("events")}
            if "streaming_url" not in existing_cols:
                with db.engine.begin() as conn:
                    conn.execute(db.text("ALTER TABLE events ADD COLUMN streaming_url VARCHAR(400)"))

        if "bookings" in inspector.get_table_names():
            existing_cols = {c["name"] for c in inspector.get_columns("bookings")}
            if "original_user_id" not in existing_cols:
                with db.engine.begin() as conn:
                    conn.execute(db.text("ALTER TABLE bookings ADD COLUMN original_user_id INTEGER"))
            # Backfill: for any booking created before this column existed, the original
            # purchaser IS the current owner (no transfer could have happened yet).
            db.session.execute(db.text(
                "UPDATE bookings SET original_user_id = user_id WHERE original_user_id IS NULL"))
            db.session.commit()

        if "booking_items" in inspector.get_table_names():
            existing_cols = {c["name"] for c in inspector.get_columns("booking_items")}
            if "is_streaming" not in existing_cols:
                with db.engine.begin() as conn:
                    conn.execute(db.text("ALTER TABLE booking_items ADD COLUMN is_streaming BOOLEAN DEFAULT 0"))
        # "ticket_transfers" table itself is created by db.create_all() above (new model)

        # Events explicitly exempt from the "<₹1,000" cap by the person's own instructions —
        # ULLAS (flagship VVIP), and Youth Development Program (intentional ₹1,999 exception).
        PRICE_CAP_EXEMPT_TITLES = ("ULLAS", "Youth Development Program")

        # Enforce "every event except the exempt ones above is priced strictly below ₹1,000"
        # across ALL events, including ones seeded before this rule existed. This is safe:
        # BookingItem stores its own price snapshot at booking time, so past bookings/receipts
        # are unaffected — only the live TicketType price (what NEW bookings would pay) changes.
        # Idempotent: once every non-exempt ticket is below ₹1,000 this loop has nothing to do.
        offending = (TicketType.query.join(Event)
                     .filter(Event.title.notin_(PRICE_CAP_EXEMPT_TITLES), TicketType.price >= 1000,
                             TicketType.is_streaming.isnot(True)).all())
        if offending:
            events_touched = {}
            for tt in offending:
                events_touched.setdefault(tt.event_id, []).append(tt)
            for event_id, tts in events_touched.items():
                all_tts_for_event = [t for t in TicketType.query.filter_by(event_id=event_id).all()
                                      if not t.is_streaming]
                if not all_tts_for_event:
                    continue
                max_price = max(t.price for t in all_tts_for_event)
                scale = 950 / max_price
                for t in all_tts_for_event:
                    t.price = round(t.price * scale, -1) or 10  # nearest ₹10, never 0
            db.session.commit()

        # ---------- Phase 4: livestream/online access — event_type + access window ----------
        events_cols = {c["name"] for c in inspector.get_columns("events")}
        with db.engine.begin() as conn:
            if "event_type" not in events_cols:
                conn.execute(db.text("ALTER TABLE events ADD COLUMN event_type VARCHAR(20) DEFAULT 'physical'"))
            if "access_start" not in events_cols:
                conn.execute(db.text("ALTER TABLE events ADD COLUMN access_start DATETIME"))
            if "access_end" not in events_cols:
                conn.execute(db.text("ALTER TABLE events ADD COLUMN access_end DATETIME"))
            if "organizer_user_id" not in events_cols:
                conn.execute(db.text("ALTER TABLE events ADD COLUMN organizer_user_id INTEGER"))
            if "is_published" not in events_cols:
                conn.execute(db.text("ALTER TABLE events ADD COLUMN is_published BOOLEAN DEFAULT 1"))
            if "parking_available" not in events_cols:
                conn.execute(db.text("ALTER TABLE events ADD COLUMN parking_available BOOLEAN DEFAULT 0"))
            if "parking_price" not in events_cols:
                conn.execute(db.text("ALTER TABLE events ADD COLUMN parking_price FLOAT"))
            if "parking_info" not in events_cols:
                conn.execute(db.text("ALTER TABLE events ADD COLUMN parking_info VARCHAR(300)"))
        # Any event that already has a streaming ticket type was already effectively
        # "hybrid" under the old auto-generated behavior — preserve that, don't demote
        # it to physical-only just because the column is new.
        for event in Event.query.all():
            if event.has_streaming_ticket and event.event_type == "physical":
                event.event_type = "hybrid"
        db.session.commit()

        # ---------- Phase 2: Eventify Connect (profiles + follow) ----------
        if "users" in inspector.get_table_names():
            existing_cols = {c["name"] for c in inspector.get_columns("users")}
            new_cols = {
                "username": "VARCHAR(30)",
                "bio": "VARCHAR(280)",
                "city": "VARCHAR(60)",
                "profile_visibility": "VARCHAR(20) DEFAULT 'public'",
                "allow_follow": "BOOLEAN DEFAULT 1",
                "attendance_visibility": "VARCHAR(20) DEFAULT 'public'",
                "allow_say_hi": "VARCHAR(20) DEFAULT 'shared_events'",
                "points": "INTEGER DEFAULT 0",
                "referral_code": "VARCHAR(12)",
                "is_plus": "BOOLEAN DEFAULT 0",
                "is_verified": "BOOLEAN DEFAULT 0",
            }
            with db.engine.begin() as conn:
                for col_name, col_type in new_cols.items():
                    if col_name not in existing_cols:
                        conn.execute(db.text(f"ALTER TABLE users ADD COLUMN {col_name} {col_type}"))

            # Backfill usernames for any user who doesn't have one yet (e.g. every
            # existing user the first time this migration runs), derived from their
            # email, de-duplicated by appending their id on collision.
            for u in User.query.filter(db.or_(User.username.is_(None), User.username == "")).all():
                base = "".join(ch for ch in u.email.split("@")[0].lower() if ch.isalnum()) or f"user{u.id}"
                candidate = base
                suffix = 1
                while User.query.filter(User.username == candidate, User.id != u.id).first():
                    suffix += 1
                    candidate = f"{base}{suffix}"
                u.username = candidate
            db.session.commit()

        # "interests" and "follows" tables themselves are created by db.create_all() above
        DEFAULT_INTERESTS = [
            ("Music", "🎵"), ("Comedy", "😂"), ("Technology", "💻"), ("AI", "🤖"),
            ("Gaming", "🎮"), ("Sports", "⚽"), ("Photography", "📸"), ("Art", "🎨"),
            ("Travel", "✈️"), ("Food", "🍔"), ("Fitness", "🏋️"), ("Business", "💼"),
            ("Entrepreneurship", "🚀"), ("Theatre", "🎭"), ("Education", "🎓"),
            ("Networking", "🤝"), ("Movies", "🎬"), ("Culture", "🏛️"),
        ]
        existing_interest_names = {i.name for i in Interest.query.all()}
        for name, emoji in DEFAULT_INTERESTS:
            if name not in existing_interest_names:
                db.session.add(Interest(name=name, emoji=emoji))
        db.session.commit()

        # ---------- Phase 7: notifications, gamification, referrals, groups/polls, Say Hi ----------
        # (users columns for points/referral_code/is_plus/allow_say_hi were added in the
        # earliest users-column ALTER block above, since every User.query needs them to exist)

        # Backfill a unique referral code for every user who doesn't have one yet.
        for u in User.query.filter(db.or_(User.referral_code.is_(None), User.referral_code == "")).all():
            for _ in range(5):
                candidate = "".join(random.choices(string.ascii_uppercase + string.digits, k=7))
                if not User.query.filter(User.referral_code == candidate, User.id != u.id).first():
                    u.referral_code = candidate
                    break
        db.session.commit()

        # ---------- Phase 8: unified report queue status ----------
        for table_name in ("comment_reports", "review_reports"):
            if table_name in inspector.get_table_names():
                cols = {c["name"] for c in inspector.get_columns(table_name)}
                if "status" not in cols:
                    with db.engine.begin() as conn:
                        conn.execute(db.text(f"ALTER TABLE {table_name} ADD COLUMN status VARCHAR(20) DEFAULT 'pending'"))

        DEFAULT_BADGES = [
            ("early_bird", "Early Bird", "🎟️", "Booked a ticket 7+ days before the event"),
            ("weekend_warrior", "Weekend Warrior", "🔥", "Attended 3 or more events"),
            ("music_explorer", "Music Explorer", "🎵", "Booked 3+ Music category events"),
            ("comedy_addict", "Comedy Addict", "😂", "Booked 3+ Comedy/Entertainment events"),
            ("tech_enthusiast", "Tech Enthusiast", "💻", "Booked 3+ Technology events"),
            ("city_explorer", "City Explorer", "🌆", "Attended events in 3+ different cities"),
            ("eventify_veteran", "Eventify Veteran", "🏆", "Attended 10 or more events"),
            ("helpful_reviewer", "Helpful Reviewer", "⭐", "Left 3 or more reviews"),
        ]
        existing_badge_codes = {b.code for b in Badge.query.all()}
        for code, name, emoji, desc in DEFAULT_BADGES:
            if code not in existing_badge_codes:
                db.session.add(Badge(code=code, name=name, emoji=emoji, description=desc))
        db.session.commit()

def initialize_required_accounts():
    """Create/update the protected Eventify admin and organizer accounts
    when explicitly enabled through environment variables."""
    with app.app_context():
        admin_email = os.environ.get("EVENTIFY_ADMIN_EMAIL")
        admin_password = os.environ.get("EVENTIFY_ADMIN_PASSWORD")
        organizer_email = os.environ.get("EVENTIFY_ORGANIZER_EMAIL")
        organizer_password = os.environ.get("EVENTIFY_ORGANIZER_PASSWORD")

        if admin_email and admin_password:
            admin = User.query.filter_by(email=admin_email.lower()).first()
            if not admin:
                admin = User(
                    name="Eventify Admin",
                    email=admin_email.lower(),
                    role="admin",
                    status="active"
                )
                admin.set_password(admin_password)
                db.session.add(admin)
            else:
                admin.role = "admin"
                admin.status = "active"
                admin.set_password(admin_password)

        if organizer_email and organizer_password:
            organizer = User.query.filter_by(email=organizer_email.lower()).first()
            if not organizer:
                organizer = User(
                    name="Eventify Organizer",
                    email=organizer_email.lower(),
                    role="organizer",
                    status="active"
                )
                organizer.set_password(organizer_password)
                db.session.add(organizer)
            else:
                organizer.role = "organizer"
                organizer.status = "active"
                organizer.set_password(organizer_password)

        db.session.commit()


if os.environ.get("EVENTIFY_INITIALIZE_ACCOUNTS") == "true":
    initialize_required_accounts()
run_migrations()

login_manager = LoginManager(app)
login_manager.login_view = "login"
login_manager.login_message = "Please log in to continue."
login_manager.login_message_category = "warning"


@login_manager.user_loader
def load_user(user_id):
    return db.session.get(User, int(user_id))


# ---------- helpers ----------

def admin_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not current_user.is_authenticated or not current_user.is_admin:
            flash("Admin access required.", "danger")
            return redirect(url_for("admin_login"))
        return f(*args, **kwargs)
    return wrapper


def organizer_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not current_user.is_authenticated or current_user.role != "organizer":
            flash("Organizer access required.", "danger")
            return redirect(url_for("organizer_login"))
        return f(*args, **kwargs)
    return wrapper


def evaluate_badges(user):
    """Check every badge's criteria against real booking/review data and award any newly
    earned ones. Returns the list of newly awarded Badge objects (for notifications).
    Deliberately re-evaluates from scratch each call rather than tracking partial state —
    the data set is small enough that this stays cheap, and it can never drift out of sync."""
    newly_awarded = []

    def award(code):
        badge = Badge.query.filter_by(code=code).first()
        if not badge:
            return
        if not UserBadge.query.filter_by(user_id=user.id, badge_id=badge.id).first():
            db.session.add(UserBadge(user_id=user.id, badge_id=badge.id))
            newly_awarded.append(badge)

    confirmed = (Booking.query.join(Payment, Payment.booking_id == Booking.id)
                 .filter(Booking.user_id == user.id, Booking.status == "confirmed",
                         Payment.status == "success").all())

    if any((b.event.date - b.booking_date.date()).days >= 7 for b in confirmed):
        award("early_bird")

    attended_event_ids = {b.event_id for b in confirmed if b.event.date < date.today()}
    if len(attended_event_ids) >= 3:
        award("weekend_warrior")
    if len(attended_event_ids) >= 10:
        award("eventify_veteran")

    cat_counts = {}
    for b in confirmed:
        cat_counts[b.event.category] = cat_counts.get(b.event.category, 0) + 1
    if cat_counts.get("Music", 0) >= 3:
        award("music_explorer")
    if cat_counts.get("Entertainment", 0) >= 3:
        award("comedy_addict")
    if cat_counts.get("Technology", 0) >= 3:
        award("tech_enthusiast")

    if len({b.event.location for b in confirmed}) >= 3:
        award("city_explorer")

    if Review.query.filter_by(user_id=user.id, is_deleted=False).count() >= 3:
        award("helpful_reviewer")

    return newly_awarded


def process_referral_if_qualifying(user, booking):
    """Called right after a booking is confirmed. If this user was referred and this is
    their first-ever confirmed+paid booking, reward both sides exactly once."""
    referral = Referral.query.filter_by(referred_user_id=user.id, rewarded=False).first()
    if not referral:
        return
    prior_confirmed = (Booking.query.join(Payment, Payment.booking_id == Booking.id)
                        .filter(Booking.user_id == user.id, Booking.status == "confirmed",
                                Payment.status == "success", Booking.id != booking.id).count())
    if prior_confirmed > 0:
        return  # not their first booking — referral already missed its window
    referrer = User.query.get(referral.referrer_id)
    if not referrer:
        return
    award_points(referrer, 200, f"Referral bonus — {user.name} completed their first booking")
    award_points(user, 200, "Referral bonus — welcome to Eventify")
    referral.rewarded = True
    referral.qualifying_booking_id = booking.id
    notify(referrer.id, "referral", "Referral bonus earned!",
           f"{user.name} completed their first booking — you both earned 200 points.",
           link=url_for("profile"))
    notify(user.id, "referral", "Welcome bonus applied!",
           "Thanks for joining via a referral — you earned 200 points.", link=url_for("profile"))


def compute_match_score(a, b):
    """Rule-based match score — no sensitive attributes, just shared event attendance,
    shared interests, same city, and mutual follows, exactly as scoped in the brief."""
    score = 40
    reasons = ["Both attending this event"]
    a_interest_ids = {i.id for i in a.interests}
    shared_interests = {i.id for i in b.interests} & a_interest_ids
    shared_names = [i.name for i in a.interests if i.id in shared_interests]
    if shared_names:
        score += min(len(shared_names) * 15, 30)
        reasons.append(f"{len(shared_names)} shared interest(s)")
    if a.city and b.city and a.city.strip().lower() == b.city.strip().lower():
        score += 15
        reasons.append(f"Both based in {a.city}")
    a_following = {f.followed_id for f in Follow.query.filter_by(follower_id=a.id).all()}
    b_following = {f.followed_id for f in Follow.query.filter_by(follower_id=b.id).all()}
    common = a_following & b_following
    if common:
        score += min(len(common) * 5, 15)
        reasons.append(f"{len(common)} mutual follow(s)")
    return min(score, 99), reasons, shared_names[:2]


def recommend_events_for(user, limit=6):
    """Rule-based 'For You' recommendations — no ML model, just honest signals from
    real data: interests, past booked categories, wishlist categories, followed
    organizers' events, and same-city events. Scored and ranked, ties broken by date."""
    if not user or not user.is_authenticated:
        return Event.query.filter_by(is_published=True, is_trending=True).limit(limit).all()

    candidates = Event.query.filter(Event.is_published == True, Event.date >= date.today()).all()  # noqa: E712
    booked_event_ids = {b.event_id for b in Booking.query.filter_by(user_id=user.id).all()}
    candidates = [e for e in candidates if e.id not in booked_event_ids]

    interest_names = {i.name.lower() for i in user.interests}
    booked_categories = {b.event.category for b in Booking.query.filter_by(user_id=user.id).all()}
    wishlist_categories = {w.event.category for w in Wishlist.query.filter_by(user_id=user.id).all()}
    followed_organizer_ids = {f.followed_id for f in Follow.query.filter_by(follower_id=user.id).all()}

    scored = []
    for e in candidates:
        score = 0
        if e.category.lower() in interest_names:
            score += 30
        if e.category in booked_categories:
            score += 20
        if e.category in wishlist_categories:
            score += 15
        if e.organizer_user_id and e.organizer_user_id in followed_organizer_ids:
            score += 25
        if user.city and e.location.strip().lower() == user.city.strip().lower():
            score += 15
        if e.is_trending:
            score += 5
        if score > 0:
            scored.append((score, e))

    scored.sort(key=lambda pair: (-pair[0], pair[1].date))
    results = [e for _, e in scored[:limit]]
    if len(results) < limit:  # pad with trending events if personalized signal is thin
        fallback = [e for e in candidates if e.is_trending and e not in results]
        results += fallback[:limit - len(results)]
    return results


def gen_ref(prefix="EVT"):
    return prefix + "-" + "".join(random.choices(string.ascii_uppercase + string.digits, k=8))


def qr_data_uri(text):
    img = qrcode.make(text)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    b64 = base64.b64encode(buf.getvalue()).decode("ascii")
    return f"data:image/png;base64,{b64}"


# ---------- Stage 2 helpers: coupons, offers, notifications ----------

MEAL_OPTIONS = ["Pizza", "Burger", "Sandwich", "Fries", "Soft Drink", "Snacks"]


def is_premium_ticket_name(name):
    """Ticket types are free-text (Regular/VIP/Premium/...); we treat any type
    whose name contains 'premium' as qualifying for the premium meal offer."""
    return "premium" in (name or "").lower()


def physical_qty(lines):
    """Total quantity of NON-streaming tickets — the only ones that consume venue capacity."""
    return sum(q for tt, q, _ in lines if not tt.is_streaming)


def compute_meal_offer(lines):
    """lines: list of (TicketType, qty, line_total). Returns a dict describing
    which free-meal offer (if any) this ticket selection qualifies for.
    This is a promotional benefit only — it never changes the amount charged.
    Live Streaming tickets don't count: the meal offer is for physical attendees."""
    physical_lines = [(tt, qty, lt) for tt, qty, lt in lines if not tt.is_streaming]
    total_qty = sum(q for _, q, _ in physical_lines)
    has_premium = any(is_premium_ticket_name(tt.name) and qty > 0 for tt, qty, _ in physical_lines)
    if has_premium:
        return {"eligible": True, "reason": "premium", "label": "Premium Ticket Offer — FREE MEAL 🎁"}
    if total_qty >= 2:
        return {"eligible": True, "reason": "multi", "label": "Book 2+ Tickets — FREE MEAL 🎉"}
    return {"eligible": False, "reason": None, "label": None}


def compute_pricing(lines, coupon_code=None):
    """Recomputes subtotal/discount/fee/total entirely server-side.
    `lines` come from ticket-type IDs/quantities validated against the DB —
    the price and discount are NEVER trusted from the browser."""
    subtotal = sum(line_total for _, _, line_total in lines)
    discount = 0.0
    applied_coupon = None
    if coupon_code:
        coupon = Coupon.query.filter(db.func.lower(Coupon.code) == coupon_code.lower(),
                                      Coupon.active == True).first()  # noqa: E712
        if coupon:
            valid, _reason = coupon.is_valid_for(subtotal)
            if valid:
                discount = coupon.compute_discount(subtotal)
                applied_coupon = coupon.code
    discounted_subtotal = round(subtotal - discount, 2)
    fee = round(discounted_subtotal * 0.05, 2)
    total = round(discounted_subtotal + fee, 2)
    return {
        "subtotal": subtotal,
        "discount": discount,
        "applied_coupon": applied_coupon,
        "fee": fee,
        "total": total,
    }


def get_todays_bookings(user):
    """Confirmed bookings this user holds for events happening today."""
    if not user or not user.is_authenticated or getattr(user, "is_admin", False):
        return []
    today = date.today()
    return (Booking.query.join(Event)
            .filter(Booking.user_id == user.id, Booking.status == "confirmed", Event.date == today)
            .all())


@app.context_processor
def inject_globals():
    celebrate_login = session.pop("celebrate_login", False)
    todays_bookings = get_todays_bookings(current_user) if current_user.is_authenticated else []
    unread_notif_count = 0
    if current_user.is_authenticated:
        unread_notif_count = Notification.query.filter_by(user_id=current_user.id, is_read=False).count()
    return {
        "current_year": datetime.now(timezone.utc).year,
        "celebrate_login": celebrate_login,
        "todays_bookings": todays_bookings,
        "today": date.today(),
        "unread_notif_count": unread_notif_count,
    }


# ---------- public pages ----------

@app.route("/")
def home():
    categories = Category.query.all()

    featured = (
        Event.query
        .filter_by(is_published=True)
        .order_by(Event.created_at.desc())
        .limit(6)
        .all()
    )

    trending = (
        Event.query
        .filter_by(is_trending=True, is_published=True)
        .limit(4)
        .all()
    )

    upcoming = (
        Event.query
        .filter(
            Event.date >= date.today(),
            Event.is_published == True
        )
        .order_by(Event.date.asc())
        .limit(6)
        .all()
    )

    ullas = Event.query.filter_by(title="ULLAS").first()

    cities = [
        row[0]
        for row in (
            db.session
            .query(Event.location)
            .filter_by(is_published=True)
            .distinct()
            .order_by(Event.location)
            .all()
        )
    ]

    stats = {
        "users": User.query.filter_by(role="user").count() + 9850,
        "events": Event.query.filter_by(is_published=True).count() + 470,
        "tickets": BookingItem.query.count() * 3 + 24500,
        "cities": (
            db.session
            .query(Event.location)
            .filter_by(is_published=True)
            .distinct()
            .count()
            + 42
        ),
    }

    return render_template(
        "index.html",
        categories=categories,
        featured=featured,
        trending=trending,
        upcoming=upcoming,
        stats=stats,
        ullas=ullas,
        cities=cities
    )


# ---------- role selection portal ----------

@app.route("/portal")
def portal():
    mode = request.args.get("mode", "login").lower()

    if mode not in {"login", "signup"}:
        mode = "login"

    return render_template("role_select.html", mode=mode)


AI_CATEGORY_SYNONYMS = {
    "music": "Music", "concert": "Music", "gig": "Music",
    "comedy": "Entertainment", "standup": "Entertainment", "stand-up": "Entertainment",
    "tech": "Technology", "technology": "Technology", "coding": "Technology",
    "sport": "Sports", "sports": "Sports", "football": "Sports", "cricket": "Sports",
    "workshop": "Workshop", "business": "Business", "startup": "Business",
    "college": "College", "cultural": "Cultural", "food": "Food & Lifestyle",
    "education": "Education", "student": "Education", "students": "Education",
}


def parse_ai_query(query):
    """Rule-based natural-language parsing — NOT an LLM call. Extracts a price cap,
    a category, a rough date window, and a location from common phrasings, and
    reports back exactly what it matched so the person can see how the query was
    interpreted rather than trusting an opaque black box."""
    q = query.lower()
    filters = {"max_price": None, "category": None, "location": None,
               "date_from": None, "date_to": None, "matched_terms": []}

    price_match = re.search(r"(?:under|below|less than|within)\s*(?:rs\.?|₹|inr)?\s*(\d+)", q)
    if price_match:
        filters["max_price"] = float(price_match.group(1))
        filters["matched_terms"].append(f"under ₹{price_match.group(1)}")

    for keyword, category_name in AI_CATEGORY_SYNONYMS.items():
        if keyword in q:
            filters["category"] = category_name
            filters["matched_terms"].append(category_name.lower())
            break

    today = date.today()
    if "this weekend" in q or "weekend" in q:
        days_to_sat = (5 - today.weekday()) % 7
        filters["date_from"] = today + timedelta(days=days_to_sat)
        filters["date_to"] = filters["date_from"] + timedelta(days=1)
        filters["matched_terms"].append("this weekend")
    elif "today" in q:
        filters["date_from"] = filters["date_to"] = today
        filters["matched_terms"].append("today")
    elif "tomorrow" in q:
        filters["date_from"] = filters["date_to"] = today + timedelta(days=1)
        filters["matched_terms"].append("tomorrow")
    elif "this month" in q:
        filters["date_from"] = today
        next_month = today.replace(day=28) + timedelta(days=4)
        filters["date_to"] = next_month.replace(day=1) - timedelta(days=1)
        filters["matched_terms"].append("this month")
    else:
        for day_name in ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]:
            if day_name in q:
                target = list(["monday", "tuesday", "wednesday", "thursday", "friday",
                                "saturday", "sunday"]).index(day_name)
                days_ahead = (target - today.weekday()) % 7
                days_ahead = days_ahead or 7  # "this saturday" means the upcoming one, not today
                filters["date_from"] = filters["date_to"] = today + timedelta(days=days_ahead)
                filters["matched_terms"].append(f"this {day_name}")
                break

    known_cities = {row[0].lower(): row[0] for row in
                     db.session.query(Event.location).filter_by(is_published=True).distinct().all()}
    for city_lower, city_proper in known_cities.items():
        if city_lower in q:
            filters["location"] = city_proper
            filters["matched_terms"].append(f"in {city_proper}")
            break

    return filters


@app.route("/ai-search", methods=["GET", "POST"])
def ai_search():
    query = request.values.get("q", "").strip()
    results, filters = [], None
    if query:
        filters = parse_ai_query(query)
        ev_query = Event.query.filter_by(is_published=True)
        if filters["category"]:
            ev_query = ev_query.join(Category).filter(Category.name == filters["category"])
        if filters["location"]:
            ev_query = ev_query.filter(Event.location == filters["location"])
        if filters["date_from"]:
            ev_query = ev_query.filter(Event.date >= filters["date_from"])
        if filters["date_to"]:
            ev_query = ev_query.filter(Event.date <= filters["date_to"])
        candidates = ev_query.all()
        if filters["max_price"]:
            candidates = [e for e in candidates if e.min_price <= filters["max_price"]]
        results = sorted(candidates, key=lambda e: e.date)
    return render_template("ai_search.html", query=query, results=results, filters=filters)


CITY_COORDINATES = {
    # Approximate city-centre coordinates — NOT precise venue geocoding. Good enough for a
    # discovery map at city zoom level; would need a real geocoding API for venue-accurate pins.
    "lucknow": (26.8467, 80.9462), "delhi": (28.6139, 77.2090), "mumbai": (19.0760, 72.8777),
    "bengaluru": (12.9716, 77.5946), "bangalore": (12.9716, 77.5946), "pune": (18.5204, 73.8567),
    "chennai": (13.0827, 80.2707), "goa": (15.2993, 74.1240), "hyderabad": (17.3850, 78.4867),
    "kolkata": (22.5726, 88.3639), "jaipur": (26.9124, 75.7873), "ahmedabad": (23.0225, 72.5714),
    "chandigarh": (30.7333, 76.7794), "kanpur": (26.4499, 80.3319), "noida": (28.5355, 77.3910),
    "gurugram": (28.4595, 77.0266), "gurgaon": (28.4595, 77.0266),
}


@app.route("/discover")
def discover_map():
    events_list = Event.query.filter_by(is_published=True).all()
    markers = []
    for e in events_list:
        coords = CITY_COORDINATES.get(e.location.strip().lower())
        if not coords:
            continue
        markers.append({
            "id": e.id, "title": e.title, "category": e.category, "location": e.location,
            "date": e.date.strftime("%d %b %Y"), "price": e.min_price, "image": e.image,
            "lat": coords[0], "lng": coords[1],
        })
    unmatched = len(events_list) - len(markers)
    return render_template("discover_map.html", markers=markers, unmatched=unmatched)


@app.route("/events")
def events():
    q = request.args.get("q", "").strip()
    category = request.args.get("category", "")
    location = request.args.get("location", "")
    sort = request.args.get("sort", "newest")
    max_price = request.args.get("max_price", "").strip()
    date_filter = request.args.get("date", "").strip()

    query = Event.query.filter_by(is_published=True)
    if q:
        query = query.filter(Event.title.ilike(f"%{q}%"))
    if location:
        query = query.filter(Event.location.ilike(f"%{location}%"))
    if category:
        query = query.join(Category).filter(Category.name == category)
    if date_filter:
        try:
            parsed_date = datetime.strptime(date_filter, "%Y-%m-%d").date()
            query = query.filter(Event.date == parsed_date)
        except ValueError:
            pass  # ignore malformed date rather than erroring the whole page

    all_events = query.all()

    if max_price:
        try:
            cap = float(max_price)
            all_events = [e for e in all_events if e.min_price <= cap]
        except ValueError:
            pass

    if sort == "price_low":
        all_events.sort(key=lambda e: e.min_price)
    elif sort == "price_high":
        all_events.sort(key=lambda e: e.min_price, reverse=True)
    elif sort == "popular":
        all_events.sort(key=lambda e: e.booked_seats, reverse=True)
    else:
        all_events.sort(key=lambda e: e.created_at, reverse=True)

    categories = Category.query.all()
    locations = sorted({e.location for e in Event.query.filter_by(is_published=True).all()})
    return render_template("events.html", events=all_events, categories=categories,
                            locations=locations, q=q, category=category,
                            location=location, sort=sort, max_price=max_price, date_filter=date_filter)


def attending_users_for(event, viewer):
    """Confirmed-booking users for this event, filtered to those whose
    attendance_visibility allows the given viewer to see them. Never touches
    ticket/QR/payment data — names and avatars only."""
    users = (User.query.join(Booking, Booking.user_id == User.id)
             .filter(Booking.event_id == event.id, Booking.status == "confirmed")
             .distinct().all())
    return [u for u in users if u.attendance_visible_to(viewer)]


@app.route("/events/<int:event_id>")
def event_detail(event_id):
    event = Event.query.get_or_404(event_id)
    if not event.is_published:
        is_owner_organizer = (current_user.is_authenticated and current_user.role == "organizer"
                               and event.organizer_user_id == current_user.id)
        if not (is_owner_organizer or (current_user.is_authenticated and current_user.is_admin)):
            abort(404)
    related = Event.query.filter(Event.category_id == event.category_id,
                                  Event.id != event.id, Event.is_published == True).limit(3).all()  # noqa: E712
    in_wishlist = False
    if current_user.is_authenticated:
        in_wishlist = Wishlist.query.filter_by(user_id=current_user.id, event_id=event.id).first() is not None

    attendees = attending_users_for(event, current_user)
    my_reaction = None
    if current_user.is_authenticated:
        r = EventReaction.query.filter_by(event_id=event.id, user_id=current_user.id).first()
        my_reaction = r.reaction_type if r else None
    reaction_counts = {rt: 0 for rt in REACTION_TYPES}
    for rt, count in (db.session.query(EventReaction.reaction_type, db.func.count(EventReaction.id))
                       .filter_by(event_id=event.id).group_by(EventReaction.reaction_type).all()):
        reaction_counts[rt] = count

    top_comments = (EventComment.query.filter_by(event_id=event.id, parent_id=None)
                     .order_by(EventComment.created_at.desc()).all())

    reviews = event.visible_reviews
    my_review = None
    if current_user.is_authenticated:
        my_review = Review.query.filter_by(event_id=event.id, user_id=current_user.id, is_deleted=False).first()
    can_review = event.is_reviewable_by(current_user)

    matches = []
    if current_user.is_authenticated:
        already_said_hi_ids = {sh.to_user_id for sh in SayHi.query.filter_by(
            event_id=event.id, from_user_id=current_user.id).all()}
        blocked_ids = {b.blocked_id for b in Block.query.filter_by(blocker_id=current_user.id).all()}
        candidates = [u for u in attendees if u.id != current_user.id and u.id not in blocked_ids]
        for u in candidates:
            score, reasons, shared_icons = compute_match_score(current_user, u)
            matches.append({"user": u, "score": score, "reasons": reasons,
                             "already_said_hi": u.id in already_said_hi_ids})
        matches.sort(key=lambda m: m["score"], reverse=True)
        matches = matches[:3]

    groups = EventGroup.query.filter_by(event_id=event.id).order_by(EventGroup.created_at.desc()).limit(3).all()

    return render_template("event_detail.html", event=event, related=related, in_wishlist=in_wishlist,
                            attendees=attendees, my_reaction=my_reaction, reaction_counts=reaction_counts,
                            reaction_types=REACTION_TYPES, comments=top_comments,
                            reviews=reviews, my_review=my_review, can_review=can_review,
                            matches=matches, groups=groups, say_hi_templates=SAY_HI_TEMPLATES)


@app.route("/events/<int:event_id>/attendees")
def event_attendees(event_id):
    event = Event.query.get_or_404(event_id)
    people = attending_users_for(event, current_user)
    return render_template("event_attendees.html", event=event, people=people)


@app.route("/events/<int:event_id>/react", methods=["POST"])
@login_required
def event_react(event_id):
    event = Event.query.get_or_404(event_id)
    reaction_type = request.form.get("reaction_type")
    if reaction_type not in REACTION_TYPES:
        abort(400)
    existing = EventReaction.query.filter_by(event_id=event.id, user_id=current_user.id).first()
    if existing and existing.reaction_type == reaction_type:
        db.session.delete(existing)  # tapping the same reaction again removes it
        db.session.commit()
    elif existing:
        existing.reaction_type = reaction_type  # switch to a different reaction
        db.session.commit()
    else:
        db.session.add(EventReaction(event_id=event.id, user_id=current_user.id, reaction_type=reaction_type))
        db.session.commit()
    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        counts = {rt: 0 for rt in REACTION_TYPES}
        for rt, count in (db.session.query(EventReaction.reaction_type, db.func.count(EventReaction.id))
                           .filter_by(event_id=event.id).group_by(EventReaction.reaction_type).all()):
            counts[rt] = count
        mine = EventReaction.query.filter_by(event_id=event.id, user_id=current_user.id).first()
        return jsonify({"counts": counts, "my_reaction": mine.reaction_type if mine else None})
    return redirect(url_for("event_detail", event_id=event.id))


@app.route("/events/<int:event_id>/comments", methods=["POST"])
@login_required
def comment_add(event_id):
    event = Event.query.get_or_404(event_id)
    content = request.form.get("content", "").strip()[:500]
    parent_id = request.form.get("parent_id", type=int)
    if not content:
        flash("Comment can't be empty.", "warning")
        return redirect(url_for("event_detail", event_id=event.id))
    if parent_id:
        parent = EventComment.query.get(parent_id)
        if not parent or parent.event_id != event.id:
            abort(400)
    db.session.add(EventComment(event_id=event.id, user_id=current_user.id,
                                 content=content, parent_id=parent_id))
    db.session.commit()
    if parent_id and parent and parent.user_id != current_user.id:
        notify(parent.user_id, "comment_reply", f"{current_user.name} replied to your comment",
               content[:100], link=url_for("event_detail", event_id=event.id) + "#discussion")
        db.session.commit()
    return redirect(url_for("event_detail", event_id=event.id) + "#discussion")


@app.route("/comments/<int:comment_id>/delete", methods=["POST"])
@login_required
def comment_delete(comment_id):
    comment = EventComment.query.get_or_404(comment_id)
    if comment.user_id != current_user.id and not current_user.is_admin:
        abort(403)
    comment.is_deleted = True
    db.session.commit()
    return redirect(request.referrer or url_for("event_detail", event_id=comment.event_id))


@app.route("/comments/<int:comment_id>/like", methods=["POST"])
@login_required
def comment_like(comment_id):
    comment = EventComment.query.get_or_404(comment_id)
    existing = CommentLike.query.filter_by(comment_id=comment.id, user_id=current_user.id).first()
    if existing:
        db.session.delete(existing)
    else:
        db.session.add(CommentLike(comment_id=comment.id, user_id=current_user.id))
    db.session.commit()
    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return jsonify({"like_count": comment.like_count, "liked": existing is None})
    return redirect(request.referrer or url_for("event_detail", event_id=comment.event_id))


@app.route("/comments/<int:comment_id>/report", methods=["POST"])
@login_required
def comment_report(comment_id):
    comment = EventComment.query.get_or_404(comment_id)
    if comment.user_id == current_user.id:
        flash("You can't report your own comment.", "warning")
        return redirect(request.referrer or url_for("event_detail", event_id=comment.event_id))
    existing = CommentReport.query.filter_by(comment_id=comment.id, reporter_id=current_user.id).first()
    if not existing:
        db.session.add(CommentReport(comment_id=comment.id, reporter_id=current_user.id,
                                      reason=request.form.get("reason", "")[:200]))
        db.session.commit()
    flash("Thanks — our team will review this comment.", "success")
    return redirect(request.referrer or url_for("event_detail", event_id=comment.event_id))


@app.route("/events/<int:event_id>/reviews", methods=["POST"])
@login_required
def review_add(event_id):
    event = Event.query.get_or_404(event_id)
    if not event.is_reviewable_by(current_user):
        flash("Only verified attendees with a completed booking can review this event.", "warning")
        return redirect(url_for("event_detail", event_id=event.id) + "#reviews")

    try:
        rating = int(request.form.get("rating", 0))
    except ValueError:
        rating = 0
    if rating < 1 or rating > 5:
        flash("Please select a star rating between 1 and 5.", "warning")
        return redirect(url_for("event_detail", event_id=event.id) + "#reviews")
    content = request.form.get("content", "").strip()[:1000]

    existing = Review.query.filter_by(event_id=event.id, user_id=current_user.id).first()
    if existing:
        existing.rating = rating
        existing.content = content
        existing.is_deleted = False
        flash("Your review was updated.", "success")
    else:
        db.session.add(Review(event_id=event.id, user_id=current_user.id, rating=rating, content=content))
        award_points(current_user, 25, f"Reviewed \"{event.title}\"")
        for badge in evaluate_badges(current_user):
            notify(current_user.id, "badge", f"New badge earned: {badge.emoji} {badge.name}",
                   badge.description, link=url_for("profile"))
        flash("Thanks for your review!", "success")
    db.session.commit()
    return redirect(url_for("event_detail", event_id=event.id) + "#reviews")


@app.route("/reviews/<int:review_id>/delete", methods=["POST"])
@login_required
def review_delete(review_id):
    review = Review.query.get_or_404(review_id)
    if review.user_id != current_user.id and not current_user.is_admin:
        abort(403)
    review.is_deleted = True
    db.session.commit()
    return redirect(request.referrer or url_for("event_detail", event_id=review.event_id))


@app.route("/reviews/<int:review_id>/helpful", methods=["POST"])
@login_required
def review_helpful(review_id):
    review = Review.query.get_or_404(review_id)
    existing = ReviewHelpful.query.filter_by(review_id=review.id, user_id=current_user.id).first()
    if existing:
        db.session.delete(existing)
    else:
        db.session.add(ReviewHelpful(review_id=review.id, user_id=current_user.id))
    db.session.commit()
    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return jsonify({"helpful_count": review.helpful_count, "marked": existing is None})
    return redirect(request.referrer or url_for("event_detail", event_id=review.event_id))


@app.route("/reviews/<int:review_id>/report", methods=["POST"])
@login_required
def review_report(review_id):
    review = Review.query.get_or_404(review_id)
    if review.user_id == current_user.id:
        flash("You can't report your own review.", "warning")
        return redirect(request.referrer or url_for("event_detail", event_id=review.event_id))
    existing = ReviewReport.query.filter_by(review_id=review.id, reporter_id=current_user.id).first()
    if not existing:
        db.session.add(ReviewReport(review_id=review.id, reporter_id=current_user.id,
                                     reason=request.form.get("reason", "")[:200]))
        db.session.commit()
    flash("Thanks — our team will review this.", "success")
    return redirect(request.referrer or url_for("event_detail", event_id=review.event_id))


@app.route("/events/<int:event_id>/groups", methods=["GET", "POST"])
@login_required
def event_groups(event_id):
    event = Event.query.get_or_404(event_id)
    if request.method == "POST":
        name = request.form.get("name", "").strip()[:80]
        if not name:
            flash("Give your group a name.", "warning")
            return redirect(url_for("event_groups", event_id=event.id))
        group = EventGroup(event_id=event.id, name=name, creator_id=current_user.id)
        db.session.add(group)
        db.session.flush()
        db.session.add(EventGroupMember(group_id=group.id, user_id=current_user.id))
        db.session.commit()
        flash(f"Group \"{name}\" created — invite friends to join!", "success")
        return redirect(url_for("event_group_detail", group_id=group.id))
    groups = EventGroup.query.filter_by(event_id=event.id).order_by(EventGroup.created_at.desc()).all()
    return render_template("event_groups.html", event=event, groups=groups)


@app.route("/groups/<int:group_id>")
@login_required
def event_group_detail(group_id):
    group = EventGroup.query.get_or_404(group_id)
    if not group.is_member(current_user):
        flash("Join this group to see its discussion and polls.", "warning")
        return redirect(url_for("event_groups", event_id=group.event_id))
    polls = Poll.query.filter_by(group_id=group.id).order_by(Poll.created_at.desc()).all()
    pending_invites = GroupInvitation.query.filter_by(group_id=group.id, status="pending").all()
    member_ids = {m.user_id for m in group.members}
    invited_ids = {inv.invited_user_id for inv in pending_invites}
    return render_template("event_group_detail.html", group=group, polls=polls,
                            pending_invites=pending_invites, member_ids=member_ids, invited_ids=invited_ids)


@app.route("/groups/<int:group_id>/invite", methods=["POST"])
@login_required
def event_group_invite(group_id):
    group = EventGroup.query.get_or_404(group_id)
    if not group.is_member(current_user):
        abort(403)  # only current members can invite — enforced server-side, not just hidden in UI

    identifier = request.form.get("identifier", "").strip()
    if not identifier:
        flash("Enter a username or email to invite.", "warning")
        return redirect(url_for("event_group_detail", group_id=group.id))

    target = User.query.filter(db.or_(User.username == identifier.lstrip("@"),
                                       User.email == identifier.lower())).first()
    if not target:
        flash(f"No Eventify user found matching \"{identifier}\".", "warning")
        return redirect(url_for("event_group_detail", group_id=group.id))
    if target.id == current_user.id:
        flash("You can't invite yourself.", "warning")
        return redirect(url_for("event_group_detail", group_id=group.id))
    if group.is_member(target):
        flash(f"{target.name} is already a member.", "warning")
        return redirect(url_for("event_group_detail", group_id=group.id))

    existing = GroupInvitation.query.filter_by(group_id=group.id, invited_user_id=target.id).first()
    if existing:
        if existing.status == "pending":
            flash(f"{target.name} already has a pending invite to this group.", "warning")
        else:
            existing.status = "pending"
            existing.invited_by_id = current_user.id
            existing.responded_at = None
            db.session.commit()
            notify(target.id, "group_invite", f"{current_user.name} invited you to \"{group.name}\"",
                   link=url_for("group_invitations_inbox"))
            db.session.commit()
            flash(f"Re-invited {target.name}.", "success")
        return redirect(url_for("event_group_detail", group_id=group.id))

    db.session.add(GroupInvitation(group_id=group.id, invited_by_id=current_user.id, invited_user_id=target.id))
    db.session.commit()
    notify(target.id, "group_invite", f"{current_user.name} invited you to \"{group.name}\"",
           f"For {group.event.title}", link=url_for("group_invitations_inbox"))
    db.session.commit()
    flash(f"Invited {target.name}.", "success")
    return redirect(url_for("event_group_detail", group_id=group.id))


@app.route("/group-invitations")
@login_required
def group_invitations_inbox():
    received = (GroupInvitation.query.filter_by(invited_user_id=current_user.id)
                .order_by(GroupInvitation.created_at.desc()).all())
    return render_template("group_invitations.html", received=received)


@app.route("/group-invitations/<int:invite_id>/respond", methods=["POST"])
@login_required
def group_invitation_respond(invite_id):
    invite = GroupInvitation.query.get_or_404(invite_id)
    if invite.invited_user_id != current_user.id:
        abort(403)  # only the invited user can accept/reject their own invitation
    if invite.status != "pending":
        flash("This invitation has already been responded to.", "warning")
        return redirect(url_for("group_invitations_inbox"))

    action = request.form.get("action")
    if action == "accept":
        invite.status = "accepted"
        invite.responded_at = datetime.now(timezone.utc)
        if not invite.group.is_member(current_user):
            db.session.add(EventGroupMember(group_id=invite.group_id, user_id=current_user.id))
        db.session.commit()
        notify(invite.invited_by_id, "group", f"{current_user.name} accepted your invite to \"{invite.group.name}\"",
               link=url_for("event_group_detail", group_id=invite.group_id))
        db.session.commit()
        flash(f"You joined \"{invite.group.name}\".", "success")
    elif action == "reject":
        invite.status = "rejected"
        invite.responded_at = datetime.now(timezone.utc)
        db.session.commit()
        flash("Invitation declined.", "success")
    else:
        abort(400)
    return redirect(url_for("group_invitations_inbox"))


@app.route("/groups/<int:group_id>/join", methods=["POST"])
@login_required
def event_group_join(group_id):
    group = EventGroup.query.get_or_404(group_id)
    if not group.is_member(current_user):
        db.session.add(EventGroupMember(group_id=group.id, user_id=current_user.id))
        db.session.commit()
        if group.creator_id != current_user.id:
            notify(group.creator_id, "group", f"{current_user.name} joined \"{group.name}\"",
                   link=url_for("event_group_detail", group_id=group.id))
            db.session.commit()
        flash(f"You joined \"{group.name}\".", "success")
    return redirect(url_for("event_group_detail", group_id=group.id))


@app.route("/groups/<int:group_id>/leave", methods=["POST"])
@login_required
def event_group_leave(group_id):
    group = EventGroup.query.get_or_404(group_id)
    if group.creator_id == current_user.id:
        flash("As the creator, you can't leave your own group — you can delete it instead.", "warning")
        return redirect(url_for("event_group_detail", group_id=group.id))
    EventGroupMember.query.filter_by(group_id=group.id, user_id=current_user.id).delete()
    db.session.commit()
    flash(f"You left \"{group.name}\".", "success")
    return redirect(url_for("event_groups", event_id=group.event_id))


@app.route("/groups/<int:group_id>/delete", methods=["POST"])
@login_required
def event_group_delete(group_id):
    group = EventGroup.query.get_or_404(group_id)
    if group.creator_id != current_user.id and not current_user.is_admin:
        abort(403)
    event_id = group.event_id
    db.session.delete(group)
    db.session.commit()
    flash("Group deleted.", "success")
    return redirect(url_for("event_groups", event_id=event_id))


@app.route("/groups/<int:group_id>/polls", methods=["POST"])
@login_required
def poll_create(group_id):
    group = EventGroup.query.get_or_404(group_id)
    if not group.is_member(current_user):
        abort(403)
    question = request.form.get("question", "").strip()[:200]
    options = [o.strip()[:100] for o in request.form.getlist("options[]") if o.strip()]
    if not question or len(options) < 2:
        flash("A poll needs a question and at least 2 options.", "warning")
        return redirect(url_for("event_group_detail", group_id=group.id))
    poll = Poll(group_id=group.id, question=question, created_by=current_user.id)
    db.session.add(poll)
    db.session.flush()
    for opt in options[:6]:
        db.session.add(PollOption(poll_id=poll.id, text=opt))
    db.session.commit()
    for member in group.members:
        if member.user_id != current_user.id:
            notify(member.user_id, "poll", f"New poll in \"{group.name}\"", question,
                   link=url_for("event_group_detail", group_id=group.id))
    db.session.commit()
    return redirect(url_for("event_group_detail", group_id=group.id))


@app.route("/polls/<int:poll_id>/vote", methods=["POST"])
@login_required
def poll_vote(poll_id):
    poll = Poll.query.get_or_404(poll_id)
    if not poll.group.is_member(current_user):
        abort(403)
    option_id = request.form.get("option_id", type=int)
    option = PollOption.query.filter_by(id=option_id, poll_id=poll.id).first()
    if not option:
        abort(400)
    existing_vote_ids = [v.id for v in PollVote.query.join(PollOption)
                          .filter(PollOption.poll_id == poll.id, PollVote.user_id == current_user.id).all()]
    if existing_vote_ids:
        PollVote.query.filter(PollVote.id.in_(existing_vote_ids)).delete(synchronize_session=False)
    db.session.add(PollVote(option_id=option.id, user_id=current_user.id))
    db.session.commit()
    return redirect(url_for("event_group_detail", group_id=poll.group_id))


# ---------- Say Hi ----------

SAY_HI_TEMPLATES = [
    "Hey! I saw you're also going to this event 👋",
    "We share some interests — thought I'd say hi!",
    "Hey! Are you going with friends, or want to meet up there?",
]


@app.route("/say-hi/<int:event_id>/<int:to_user_id>", methods=["POST"])
@login_required
def say_hi_send(event_id, to_user_id):
    event = Event.query.get_or_404(event_id)
    to_user = User.query.get_or_404(to_user_id)
    if to_user.id == current_user.id:
        abort(400)
    if Block.query.filter_by(blocker_id=to_user.id, blocked_id=current_user.id).first():
        flash("You can't message this user.", "warning")
        return redirect(url_for("event_detail", event_id=event.id))
    if to_user.allow_say_hi == "no":
        flash(f"{to_user.name} isn't accepting introductions right now.", "warning")
        return redirect(url_for("event_detail", event_id=event.id))
    if to_user.allow_say_hi == "shared_events":
        i_am_going = Booking.query.filter_by(user_id=current_user.id, event_id=event.id, status="confirmed").first()
        they_are_going = Booking.query.filter_by(user_id=to_user.id, event_id=event.id, status="confirmed").first()
        if not (i_am_going and they_are_going):
            flash("Say Hi is limited to people also attending this event.", "warning")
            return redirect(url_for("event_detail", event_id=event.id))

    message = request.form.get("message", "").strip()[:200]
    if message not in SAY_HI_TEMPLATES:
        message = SAY_HI_TEMPLATES[0]  # only pre-written templates are accepted, not freeform text

    existing = SayHi.query.filter_by(event_id=event.id, from_user_id=current_user.id, to_user_id=to_user.id).first()
    if existing:
        flash("You've already said hi for this event.", "warning")
        return redirect(url_for("event_detail", event_id=event.id))

    db.session.add(SayHi(event_id=event.id, from_user_id=current_user.id, to_user_id=to_user.id, message=message))
    db.session.commit()
    notify(to_user.id, "say_hi", f"{current_user.name} said hi 👋", message,
           link=url_for("say_hi_inbox"))
    db.session.commit()
    flash(f"Sent! {to_user.name} will see your intro.", "success")
    return redirect(url_for("event_detail", event_id=event.id))


@app.route("/say-hi")
@login_required
def say_hi_inbox():
    received = SayHi.query.filter_by(to_user_id=current_user.id).order_by(SayHi.created_at.desc()).all()
    sent = SayHi.query.filter_by(from_user_id=current_user.id).order_by(SayHi.created_at.desc()).all()
    return render_template("say_hi_inbox.html", received=received, sent=sent)


@app.route("/say-hi/<int:say_hi_id>/respond", methods=["POST"])
@login_required
def say_hi_respond(say_hi_id):
    sh = SayHi.query.get_or_404(say_hi_id)
    if sh.to_user_id != current_user.id:
        abort(403)
    action = request.form.get("action")
    if action == "accept":
        sh.status = "accepted"
        notify(sh.from_user_id, "say_hi", f"{current_user.name} accepted your Say Hi!",
               link=url_for("public_profile", username=current_user.username) if current_user.username else None)
    elif action == "ignore":
        sh.status = "ignored"
    elif action == "block":
        sh.status = "blocked"
        if not Block.query.filter_by(blocker_id=current_user.id, blocked_id=sh.from_user_id).first():
            db.session.add(Block(blocker_id=current_user.id, blocked_id=sh.from_user_id))
    elif action == "report":
        sh.status = "reported"
        flash("Thanks — our team will review this.", "success")
    db.session.commit()
    return redirect(url_for("say_hi_inbox"))


# ---------- auth ----------

def generate_username(base_seed):
    base = "".join(ch for ch in base_seed.split("@")[0].lower() if ch.isalnum()) or "user"
    candidate = base
    suffix = 1
    while User.query.filter_by(username=candidate).first():
        suffix += 1
        candidate = f"{base}{suffix}"
    return candidate


def generate_referral_code():
    for _ in range(10):
        candidate = "".join(random.choices(string.ascii_uppercase + string.digits, k=7))
        if not User.query.filter_by(referral_code=candidate).first():
            return candidate
    return "".join(random.choices(string.ascii_uppercase + string.digits, k=10))  # astronomically-unlikely fallback


@app.route("/register", methods=["GET", "POST"])
def register():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard"))
    ref_code = request.args.get("ref", "").strip().upper()
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip().lower()
        phone = request.form.get("phone", "").strip()
        password = request.form.get("password", "")
        confirm = request.form.get("confirm_password", "")
        submitted_ref = request.form.get("ref_code", "").strip().upper()

        error = None
        if not name or not email or not password:
            error = "Please fill in all required fields."
        elif password != confirm:
            error = "Passwords do not match."
        elif len(password) < 6:
            error = "Password must be at least 6 characters."
        elif User.query.filter_by(email=email).first():
            error = "An account with this email already exists."

        if error:
            flash(error, "danger")
            return render_template("register.html", form=request.form, ref_code=ref_code)

        user = User(name=name, email=email, phone=phone)
        user.set_password(password)
        user.username = generate_username(email)
        user.referral_code = generate_referral_code()
        db.session.add(user)
        db.session.flush()

        # Record the referral relationship now (at signup); the actual point reward only
        # happens later, once this user completes a genuine qualifying booking — see
        # payment_verify — so referral points can't be farmed with a throwaway signup.
        if submitted_ref:
            referrer = User.query.filter_by(referral_code=submitted_ref).first()
            if referrer and referrer.id != user.id:
                db.session.add(Referral(referrer_id=referrer.id, referred_user_id=user.id))

        db.session.commit()
        flash("Account created successfully. Please log in.", "success")
        return redirect(url_for("login"))

    return render_template("register.html", form={}, ref_code=ref_code)


@app.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard"))
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        remember = bool(request.form.get("remember"))
        user = User.query.filter_by(email=email).first()
        if user and user.check_password(password) and user.role == "user":
            if user.status == "disabled":
                flash("Your account has been disabled. Contact support.", "danger")
                return render_template("login.html")
            login_user(user, remember=remember)
            session["celebrate_login"] = True
            next_page = request.args.get("next")
            return redirect(next_page or url_for("dashboard"))
        flash("Invalid email or password.", "danger")
    return render_template("login.html")


@app.route("/logout")
@login_required
def logout():
    logout_user()
    flash("You have been logged out.", "info")
    return redirect(url_for("home"))


# ---------- user dashboard ----------

@app.route("/dashboard")
@login_required
def dashboard():
    bookings = Booking.query.filter_by(user_id=current_user.id).order_by(Booking.booking_date.desc()).all()
    upcoming = [b for b in bookings if b.event.date >= date.today() and b.status == "confirmed"]
    wishlist_count = Wishlist.query.filter_by(user_id=current_user.id).count()
    tickets_used = sum(b.total_tickets for b in bookings if b.event.date < date.today())
    recommended = recommend_events_for(current_user, limit=4)
    return render_template("dashboard.html", bookings=bookings[:5], upcoming=upcoming,
                            total_bookings=len(bookings), wishlist_count=wishlist_count,
                            tickets_used=tickets_used, upcoming_count=len(upcoming),
                            recommended=recommended)


@app.route("/my-bookings")
@login_required
def my_bookings():
    bookings = Booking.query.filter_by(user_id=current_user.id).order_by(Booking.booking_date.desc()).all()
    return render_template("my_bookings.html", bookings=bookings)


@app.route("/wishlist")
@login_required
def wishlist():
    items = Wishlist.query.filter_by(user_id=current_user.id).all()
    return render_template("wishlist.html", items=items)


@app.route("/wishlist/toggle/<int:event_id>", methods=["POST"])
@login_required
def wishlist_toggle(event_id):
    existing = Wishlist.query.filter_by(user_id=current_user.id, event_id=event_id).first()
    if existing:
        db.session.delete(existing)
        db.session.commit()
        return jsonify({"status": "removed"})
    else:
        db.session.add(Wishlist(user_id=current_user.id, event_id=event_id))
        db.session.commit()
        return jsonify({"status": "added"})


@app.route("/profile", methods=["GET", "POST"])
@login_required
def profile():
    all_interests = Interest.query.order_by(Interest.name).all()
    if request.method == "POST":
        current_user.name = request.form.get("name", current_user.name).strip() or current_user.name
        current_user.phone = request.form.get("phone", current_user.phone).strip()
        current_user.bio = request.form.get("bio", "").strip()[:280]
        current_user.city = request.form.get("city", "").strip()[:60]

        new_username = request.form.get("username", "").strip().lower()
        if new_username and new_username != current_user.username:
            clean = "".join(ch for ch in new_username if ch.isalnum() or ch == "_")[:30]
            if not clean:
                flash("Username can only contain letters, numbers and underscores.", "danger")
                return redirect(url_for("profile"))
            taken = User.query.filter(User.username == clean, User.id != current_user.id).first()
            if taken:
                flash(f"Username \"{clean}\" is already taken.", "danger")
                return redirect(url_for("profile"))
            current_user.username = clean

        visibility = request.form.get("profile_visibility")
        if visibility in ("public", "followers", "private"):
            current_user.profile_visibility = visibility
        current_user.allow_follow = bool(request.form.get("allow_follow"))

        attendance_vis = request.form.get("attendance_visibility")
        if attendance_vis in ("public", "followers", "private"):
            current_user.attendance_visibility = attendance_vis

        say_hi_pref = request.form.get("allow_say_hi")
        if say_hi_pref in ("yes", "shared_events", "no"):
            current_user.allow_say_hi = say_hi_pref

        selected_ids = {int(i) for i in request.form.getlist("interests")}
        current_user.interests = [i for i in all_interests if i.id in selected_ids]

        db.session.commit()
        flash("Profile updated successfully.", "success")
        return redirect(url_for("profile"))
    return render_template("profile.html", all_interests=all_interests)


@app.route("/u/<username>")
def public_profile(username):
    user = User.query.filter_by(username=username).first_or_404()
    if user.is_admin:
        abort(404)  # admin accounts have no public social presence
    visible = user.is_visible_to(current_user)
    is_own = current_user.is_authenticated and current_user.id == user.id
    already_following = user.is_followed_by(current_user)
    events_attended = 0
    if visible:
        events_attended = (Booking.query.join(Event)
                            .filter(Booking.user_id == user.id, Booking.status == "confirmed",
                                    Event.date < date.today()).count())
    return render_template("public_profile.html", profile_user=user, visible=visible,
                            is_own=is_own, already_following=already_following,
                            events_attended=events_attended)


@app.route("/follow/<int:user_id>", methods=["POST"])
@login_required
def follow_toggle(user_id):
    target = User.query.get_or_404(user_id)
    if target.id == current_user.id:
        flash("You can't follow yourself.", "warning")
        return redirect(request.referrer or url_for("dashboard"))

    existing = Follow.query.filter_by(follower_id=current_user.id, followed_id=target.id).first()
    if existing:
        db.session.delete(existing)
        db.session.commit()
        flash(f"Unfollowed {target.name}.", "success")
    else:
        if not target.allow_follow:
            flash(f"{target.name} isn't accepting new followers.", "warning")
            return redirect(request.referrer or url_for("dashboard"))
        db.session.add(Follow(follower_id=current_user.id, followed_id=target.id))
        db.session.commit()
        notify(target.id, "follow", f"{current_user.name} started following you",
               link=url_for("public_profile", username=current_user.username) if current_user.username else None)
        db.session.commit()
        flash(f"You're now following {target.name}.", "success")
    return redirect(request.referrer or url_for("public_profile", username=target.username))


@app.route("/u/<username>/followers")
def followers_list(username):
    user = User.query.filter_by(username=username).first_or_404()
    if not user.is_visible_to(current_user):
        abort(403)
    follows = Follow.query.filter_by(followed_id=user.id).order_by(Follow.created_at.desc()).all()
    people = [f.follower for f in follows]
    return render_template("follow_list.html", profile_user=user, people=people,
                            list_kind="Followers")


@app.route("/u/<username>/following")
def following_list(username):
    user = User.query.filter_by(username=username).first_or_404()
    if not user.is_visible_to(current_user):
        abort(403)
    follows = Follow.query.filter_by(follower_id=user.id).order_by(Follow.created_at.desc()).all()
    people = [f.followed for f in follows]
    return render_template("follow_list.html", profile_user=user, people=people,
                            list_kind="Following")


@app.route("/notifications")
@login_required
def notifications():
    items = Notification.query.filter_by(user_id=current_user.id).order_by(Notification.created_at.desc()).limit(50).all()
    return render_template("notifications.html", items=items)


@app.route("/notifications/<int:notif_id>/open", methods=["POST"])
@login_required
def notification_open(notif_id):
    n = Notification.query.get_or_404(notif_id)
    if n.user_id != current_user.id:
        abort(403)
    n.is_read = True
    db.session.commit()
    return redirect(n.link or url_for("notifications"))


@app.route("/notifications/mark-all-read", methods=["POST"])
@login_required
def notifications_mark_all_read():
    Notification.query.filter_by(user_id=current_user.id, is_read=False).update({"is_read": True})
    db.session.commit()
    return redirect(request.referrer or url_for("notifications"))


@app.route("/eventify-plus")
def eventify_plus():
    return render_template("eventify_plus.html")


@app.route("/admin/users/<int:user_id>/toggle-plus", methods=["POST"])
@admin_required
def admin_toggle_plus(user_id):
    user = User.query.get_or_404(user_id)
    user.is_plus = not user.is_plus
    db.session.commit()
    flash(f"{user.name} is now {'Eventify+' if user.is_plus else 'a standard member'}.", "success")
    return redirect(request.referrer or url_for("admin_users"))


@app.route("/feed")
@login_required
def event_feed():
    """Real social activity feed — distinct from 'For You' (which recommends events you
    haven't interacted with). This shows what people you follow are actually doing:
    attending events, reviewing, commenting, reacting. Every item is real DB data,
    filtered by each poster's own privacy settings where relevant (attendance)."""
    followed_ids = [f.followed_id for f in Follow.query.filter_by(follower_id=current_user.id).all()]
    items = []

    if followed_ids:
        bookings = (Booking.query.join(Payment, Payment.booking_id == Booking.id)
                    .filter(Booking.user_id.in_(followed_ids), Booking.status == "confirmed",
                            Payment.status == "success")
                    .order_by(Booking.booking_date.desc()).limit(30).all())
        for b in bookings:
            if b.user.attendance_visible_to(current_user):
                items.append({"type": "attending", "user": b.user, "event": b.event, "at": b.booking_date})

        reviews = (Review.query.filter(Review.user_id.in_(followed_ids), Review.is_deleted == False)  # noqa: E712
                   .order_by(Review.created_at.desc()).limit(30).all())
        for r in reviews:
            items.append({"type": "review", "user": r.user, "event": r.event, "review": r, "at": r.created_at})

        comments = (EventComment.query.filter(EventComment.user_id.in_(followed_ids),
                                               EventComment.is_deleted == False)  # noqa: E712
                    .order_by(EventComment.created_at.desc()).limit(30).all())
        for c in comments:
            items.append({"type": "comment", "user": c.user, "event": c.event, "comment": c, "at": c.created_at})

        reactions = (EventReaction.query.filter(EventReaction.user_id.in_(followed_ids))
                     .order_by(EventReaction.created_at.desc()).limit(30).all())
        for rx in reactions:
            items.append({"type": "reaction", "user": rx.user, "event": rx.event,
                          "reaction_type": rx.reaction_type, "at": rx.created_at})

    items.sort(key=lambda i: i["at"], reverse=True)
    return render_template("feed.html", items=items[:40], has_follows=bool(followed_ids))


# ---------- booking flow ----------

@app.route("/book/<int:event_id>/tickets", methods=["GET", "POST"])
@login_required
def book_tickets(event_id):
    event = Event.query.get_or_404(event_id)
    if event.is_sold_out:
        flash("Sorry, this event is sold out.", "danger")
        return redirect(url_for("event_detail", event_id=event.id))

    if request.method == "POST":
        selections = {}
        total_qty = 0
        physical_qty = 0
        for tt in event.ticket_types:
            qty = int(request.form.get(f"qty_{tt.id}", 0) or 0)
            if qty > 0:
                selections[tt.id] = qty
                total_qty += qty
                if not tt.is_streaming:
                    physical_qty += qty

        if total_qty == 0:
            flash("Please select at least one ticket.", "warning")
            return redirect(url_for("book_tickets", event_id=event.id))
        if physical_qty > event.available_seats:
            flash(f"Only {event.available_seats} physical seats available.", "danger")
            return redirect(url_for("book_tickets", event_id=event.id))

        session["pending_booking"] = {"event_id": event.id, "selections": selections}
        return redirect(url_for("book_details", event_id=event.id))

    return render_template("booking_tickets.html", event=event)


@app.route("/book/<int:event_id>/details", methods=["GET", "POST"])
@login_required
def book_details(event_id):
    event = Event.query.get_or_404(event_id)
    pending = session.get("pending_booking")
    if not pending or pending.get("event_id") != event_id:
        return redirect(url_for("book_tickets", event_id=event_id))

    lines = _pending_lines(event, pending)
    meal_offer = compute_meal_offer(lines)

    if request.method == "POST":
        pending["customer_name"] = request.form.get("name", current_user.name)
        pending["customer_email"] = request.form.get("email", current_user.email)
        pending["customer_phone"] = request.form.get("phone", current_user.phone or "")

        meal_choice = request.form.get("meal_choice", "").strip()
        # Only store the meal choice if the booking genuinely qualifies AND it's
        # one of the real options — never trust an arbitrary value from the form.
        if meal_offer["eligible"] and meal_choice in MEAL_OPTIONS:
            pending["meal_choice"] = meal_choice
        else:
            pending.pop("meal_choice", None)

        session["pending_booking"] = pending
        return redirect(url_for("payment", event_id=event_id))

    display_lines = [{"name": tt.name, "price": tt.price, "qty": qty, "total": line_total}
                      for tt, qty, line_total in lines]
    pricing = compute_pricing(lines, pending.get("coupon_code"))

    return render_template("booking_details.html", event=event, lines=display_lines,
                            pricing=pricing, meal_offer=meal_offer, meal_options=MEAL_OPTIONS,
                            selected_meal=pending.get("meal_choice"))


def _pending_lines(event, pending):
    """Ticket lines built from ticket-type IDs + quantities in the session —
    the price always comes fresh from the DB, never from the browser."""
    selections = pending["selections"]
    lines = []
    for tt in event.ticket_types:
        qty = selections.get(tt.id) or selections.get(str(tt.id))
        if qty:
            line_total = qty * tt.price
            lines.append((tt, qty, line_total))
    return lines


@app.route("/book/<int:event_id>/coupon/apply", methods=["POST"])
@login_required
def coupon_apply(event_id):
    event = Event.query.get_or_404(event_id)
    pending = session.get("pending_booking")
    if not pending or pending.get("event_id") != event_id:
        return jsonify({"ok": False, "error": "Your booking session has expired."}), 400

    code = ((request.get_json(silent=True) or {}).get("code") or "").strip()
    if not code:
        return jsonify({"ok": False, "error": "Please enter a coupon code."}), 400

    coupon = Coupon.query.filter(db.func.lower(Coupon.code) == code.lower(), Coupon.active == True).first()  # noqa: E712
    if not coupon:
        return jsonify({"ok": False, "error": "Invalid or expired coupon code."}), 400

    lines = _pending_lines(event, pending)
    subtotal = sum(line_total for _, _, line_total in lines)
    valid, reason = coupon.is_valid_for(subtotal)
    if not valid:
        return jsonify({"ok": False, "error": reason}), 400

    pending["coupon_code"] = coupon.code
    session["pending_booking"] = pending

    pricing = compute_pricing(lines, coupon.code)
    discount_label = (f"₹{coupon.discount_amount:.0f} OFF" if coupon.discount_type == "flat" and coupon.discount_amount
                       else f"{coupon.discount_percent:.0f}% OFF")
    return jsonify({"ok": True, "message": f"Coupon '{coupon.code}' applied — {discount_label}!",
                     "pricing": pricing})


@app.route("/book/<int:event_id>/coupon/remove", methods=["POST"])
@login_required
def coupon_remove(event_id):
    event = Event.query.get_or_404(event_id)
    pending = session.get("pending_booking")
    if not pending or pending.get("event_id") != event_id:
        return jsonify({"ok": False, "error": "Your booking session has expired."}), 400

    pending.pop("coupon_code", None)
    session["pending_booking"] = pending

    lines = _pending_lines(event, pending)
    pricing = compute_pricing(lines, None)
    return jsonify({"ok": True, "pricing": pricing})


@app.route("/book/<int:event_id>/payment", methods=["GET"])
@login_required
def payment(event_id):
    event = Event.query.get_or_404(event_id)
    pending = session.get("pending_booking")
    if not pending or pending.get("event_id") != event_id or "customer_email" not in pending:
        return redirect(url_for("book_tickets", event_id=event_id))

    lines = _pending_lines(event, pending)
    pricing = compute_pricing(lines, pending.get("coupon_code"))
    total = pricing["total"]
    total_qty = sum(q for _, q, _ in lines)
    if total_qty == 0 or physical_qty(lines) > event.available_seats:
        flash("Seats are no longer available for this event.", "danger")
        session.pop("pending_booking", None)
        return redirect(url_for("event_detail", event_id=event.id))

    client = get_razorpay_client()
    if not client:
        flash("Online payment is not configured yet. Please contact the site admin.", "danger")
        return redirect(url_for("book_details", event_id=event.id))

    amount_paise = int(round(total * 100))

    # Reuse an already-created order if the amount hasn't changed (avoids creating
    # duplicate Razorpay orders if the user refreshes this page). Applying/removing
    # a coupon changes `total`, so a fresh order is naturally created when that happens.
    order_id = pending.get("razorpay_order_id")
    if not order_id or pending.get("razorpay_amount") != amount_paise:
        try:
            order = client.order.create({
                "amount": amount_paise,
                "currency": "INR",
                "receipt": f"evt{event.id}-u{current_user.id}-{int(datetime.now(timezone.utc).timestamp())}",
                "payment_capture": 1,
            })
        except Exception:
            flash("Could not reach the payment gateway. Please try again in a moment.", "danger")
            return redirect(url_for("book_details", event_id=event.id))
        order_id = order["id"]
        pending["razorpay_order_id"] = order_id
        pending["razorpay_amount"] = amount_paise
        session["pending_booking"] = pending

    return render_template("payment.html", event=event, pricing=pricing, total=total,
                            razorpay_key_id=RAZORPAY_KEY_ID,
                            razorpay_order_id=order_id,
                            razorpay_amount=amount_paise)


@app.route("/payment/verify", methods=["POST"])
@login_required
def payment_verify():
    """Verifies the Razorpay payment signature server-side. A booking is only ever
    created AFTER successful verification — opening the checkout window is never
    enough to mark a ticket as paid."""
    data = request.get_json(silent=True) or {}
    razorpay_order_id = data.get("razorpay_order_id")
    razorpay_payment_id = data.get("razorpay_payment_id")
    razorpay_signature = data.get("razorpay_signature")

    pending = session.get("pending_booking")
    if not pending or pending.get("razorpay_order_id") != razorpay_order_id:
        return jsonify({"ok": False, "error": "Your booking session has expired. Please start again."}), 400

    event = Event.query.get_or_404(pending["event_id"])

    client = get_razorpay_client()
    if not client:
        return jsonify({"ok": False, "error": "Payment gateway not configured."}), 500

    try:
        client.utility.verify_payment_signature({
            "razorpay_order_id": razorpay_order_id,
            "razorpay_payment_id": razorpay_payment_id,
            "razorpay_signature": razorpay_signature,
        })
    except razorpay.errors.SignatureVerificationError:
        return jsonify({"ok": False, "error": "Payment verification failed. If any amount was "
                                               "deducted, it will be auto-refunded by Razorpay."}), 400

    # Idempotency: if this order was already verified once (e.g. duplicate request), don't double-book.
    existing_payment = Payment.query.filter_by(razorpay_order_id=razorpay_order_id).first()
    if existing_payment:
        return jsonify({"ok": True, "redirect": url_for("confirmation", booking_id=existing_payment.booking_id)})

    lines = _pending_lines(event, pending)
    pricing = compute_pricing(lines, pending.get("coupon_code"))
    total = pricing["total"]
    total_qty = sum(q for _, q, _ in lines)
    phys_qty = physical_qty(lines)

    if total_qty == 0 or phys_qty > event.available_seats:
        session.pop("pending_booking", None)
        return jsonify({"ok": False, "error": "Seats are no longer available for this event.",
                         "redirect": url_for("event_detail", event_id=event.id)}), 409

    meal_offer = compute_meal_offer(lines)
    meal_choice = pending.get("meal_choice")
    if not (meal_offer["eligible"] and meal_choice in MEAL_OPTIONS):
        meal_choice = None  # never trust a stray value — only store it if still genuinely eligible

    booking = Booking(
        booking_ref=gen_ref(),
        user_id=current_user.id,
        original_user_id=current_user.id,
        event_id=event.id,
        customer_name=pending["customer_name"],
        customer_email=pending["customer_email"],
        customer_phone=pending["customer_phone"],
        total_amount=total,
        status="confirmed",
        coupon_code=pricing["applied_coupon"],
        discount_amount=pricing["discount"],
        meal_choice=meal_choice,
    )
    db.session.add(booking)
    db.session.flush()

    if pricing["applied_coupon"]:
        used_coupon = Coupon.query.filter_by(code=pricing["applied_coupon"]).first()
        if used_coupon:
            used_coupon.times_used = (used_coupon.times_used or 0) + 1

    for tt, qty, line_total in lines:
        db.session.add(BookingItem(booking_id=booking.id, ticket_type=tt.name,
                                    price=tt.price, quantity=qty, is_streaming=tt.is_streaming))

    event.booked_seats += phys_qty

    db.session.add(Payment(
        booking_id=booking.id,
        payment_method="razorpay",
        transaction_id=razorpay_payment_id,
        amount=total,
        status="success",
        razorpay_order_id=razorpay_order_id,
        razorpay_payment_id=razorpay_payment_id,
        razorpay_signature=razorpay_signature,
    ))
    db.session.commit()
    session.pop("pending_booking", None)

    # Gamification hooks — all run against the now-committed, real booking/payment rows.
    award_points(current_user, 100, f"Booked \"{event.title}\"")
    notify(current_user.id, "booking", "Booking confirmed!",
           f"Your booking for \"{event.title}\" is confirmed.", link=url_for("confirmation", booking_id=booking.id))
    process_referral_if_qualifying(current_user, booking)
    for badge in evaluate_badges(current_user):
        notify(current_user.id, "badge", f"New badge earned: {badge.emoji} {badge.name}",
               badge.description, link=url_for("profile"))
    db.session.commit()

    return jsonify({"ok": True, "redirect": url_for("confirmation", booking_id=booking.id)})


@app.route("/booking/<int:booking_id>/confirmation")
@login_required
def confirmation(booking_id):
    booking = Booking.query.get_or_404(booking_id)
    if booking.user_id != current_user.id and not current_user.is_admin:
        abort(403)
    qr = qr_data_uri(f"EVENTIFY|{booking.booking_ref}|{booking.event.title}|{booking.customer_name}")
    return render_template("confirmation.html", booking=booking, qr=qr)


@app.route("/booking/<int:booking_id>/ticket")
@login_required
def ticket(booking_id):
    booking = Booking.query.get_or_404(booking_id)
    if booking.user_id != current_user.id and not current_user.is_admin:
        abort(403)
    qr = qr_data_uri(f"EVENTIFY|{booking.booking_ref}|{booking.event.title}|{booking.customer_name}")
    return render_template("ticket.html", booking=booking, qr=qr)


# ---------- live streaming access ----------

@app.route("/booking/<int:booking_id>/stream")
@login_required
def stream_access(booking_id):
    """Gated behind: logged in + current ticket owner + booking actually paid/confirmed +
    booking includes a streaming entitlement + event still has online access enabled.
    The stream URL itself is never rendered to anyone who doesn't pass all checks."""
    booking = Booking.query.get_or_404(booking_id)
    if booking.user_id != current_user.id and not current_user.is_admin:
        abort(403)
    if booking.status != "confirmed" or not booking.payment or booking.payment.status != "success":
        abort(403)
    if not booking.has_streaming_item:
        abort(404)
    event = booking.event
    state = event.stream_state
    if state == "NOT_ENABLED":
        # Admin has since disabled online access for this event — booking data and past
        # entitlement are preserved, but access is revoked going forward.
        abort(404)
    return render_template("stream_access.html", booking=booking, event=event, state=state)


# ---------- secure ticket transfer ----------

@app.route("/booking/<int:booking_id>/transfer", methods=["GET", "POST"])
@login_required
def transfer_start(booking_id):
    booking = Booking.query.get_or_404(booking_id)
    if booking.user_id != current_user.id:
        abort(403)  # only the CURRENT owner can initiate a transfer
    if booking.status != "confirmed":
        flash("Only confirmed bookings can be transferred.", "danger")
        return redirect(url_for("my_bookings"))
    if booking.event.date < date.today():
        flash("This event has already passed — the ticket can no longer be transferred.", "danger")
        return redirect(url_for("my_bookings"))
    if booking.pending_transfer:
        flash("A transfer for this ticket is already pending.", "warning")
        return redirect(url_for("ticket", booking_id=booking.id))

    if request.method == "POST":
        to_email = request.form.get("to_email", "").strip().lower()
        to_phone = request.form.get("to_phone", "").strip()

        if not to_email or not to_phone:
            flash("Please provide the recipient's email and phone number.", "danger")
            return render_template("transfer_start.html", booking=booking)
        if to_email == current_user.email.lower():
            flash("You can't transfer a ticket to yourself.", "danger")
            return render_template("transfer_start.html", booking=booking)

        recipient = User.query.filter(db.func.lower(User.email) == to_email).first()
        if not recipient:
            flash("No Eventify account exists with that email. The recipient needs to sign up first.", "danger")
            return render_template("transfer_start.html", booking=booking)
        if recipient.id == current_user.id:
            flash("You can't transfer a ticket to yourself.", "danger")
            return render_template("transfer_start.html", booking=booking)

        transfer = TicketTransfer(
            booking_id=booking.id,
            from_user_id=current_user.id,
            to_email=to_email,
            to_phone=to_phone,
            token=secrets.token_urlsafe(32),
            status="pending",
        )
        db.session.add(transfer)
        db.session.commit()

        # No email/SMS service is configured in this project, so the verification
        # link is shown directly to the sender to pass on to the recipient.
        accept_url = url_for("transfer_accept", token=transfer.token, _external=True)
        flash("Transfer started! Share the verification link below with the recipient.", "success")
        return render_template("transfer_start.html", booking=booking, accept_url=accept_url, sent=True)

    return render_template("transfer_start.html", booking=booking)


@app.route("/booking/<int:booking_id>/transfer/cancel", methods=["POST"])
@login_required
def transfer_cancel(booking_id):
    booking = Booking.query.get_or_404(booking_id)
    if booking.user_id != current_user.id:
        abort(403)
    transfer = booking.pending_transfer
    if transfer:
        transfer.status = "cancelled"
        transfer.responded_at = datetime.now(timezone.utc)
        db.session.commit()
        flash("Transfer cancelled.", "success")
    return redirect(url_for("ticket", booking_id=booking.id))


@app.route("/transfer/accept/<token>", methods=["GET", "POST"])
@login_required
def transfer_accept(token):
    transfer = TicketTransfer.query.filter_by(token=token).first_or_404()
    booking = transfer.booking

    # Token is single-use: once it's no longer pending, it can never be accepted again.
    if transfer.status != "pending":
        flash("This transfer link is no longer valid — it may have already been used or cancelled.", "warning")
        return redirect(url_for("dashboard"))

    # Expire links after 7 days
    if (datetime.now(timezone.utc) - transfer.created_at.replace(tzinfo=timezone.utc)).days > 7:
        transfer.status = "expired"
        db.session.commit()
        flash("This transfer link has expired. Ask the sender to start a new transfer.", "warning")
        return redirect(url_for("dashboard"))

    # The logged-in account must match the email the transfer was sent to —
    # this is the "account verification" step. Ownership currently still belongs
    # to the sender, so it can't have been reused elsewhere in the meantime.
    if current_user.email.lower() != transfer.to_email.lower():
        flash("This transfer was sent to a different email address. Please log in as that account.", "danger")
        return redirect(url_for("dashboard"))

    if booking.status != "confirmed" or booking.user_id != transfer.from_user_id:
        transfer.status = "cancelled"
        db.session.commit()
        flash("This ticket is no longer eligible for transfer.", "danger")
        return redirect(url_for("dashboard"))

    if request.method == "POST":
        phone_confirm = request.form.get("phone_confirm", "").strip()
        if phone_confirm != transfer.to_phone:
            flash("The phone number you entered doesn't match — please double-check and try again.", "danger")
            return render_template("transfer_accept.html", transfer=transfer, booking=booking)

        action = request.form.get("action")
        if action == "reject":
            transfer.status = "rejected"
            transfer.responded_at = datetime.now(timezone.utc)
            db.session.commit()
            flash("Transfer declined.", "success")
            return redirect(url_for("dashboard"))

        # Accept: ownership moves to the recipient. booking_ref/id never change —
        # only user_id, so this remains the SAME ticket, just re-owned.
        booking.user_id = current_user.id
        transfer.to_user_id = current_user.id
        transfer.status = "accepted"
        transfer.responded_at = datetime.now(timezone.utc)
        db.session.commit()
        flash(f"Ticket transferred! '{booking.event.title}' is now in your account. 🎉", "success")
        return redirect(url_for("ticket", booking_id=booking.id))

    return render_template("transfer_accept.html", transfer=transfer, booking=booking)


# ---------- admin ----------

@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    if current_user.is_authenticated and current_user.is_admin:
        return redirect(url_for("admin_dashboard"))
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        admin = User.query.filter_by(email=email, role="admin").first()
        if admin and admin.check_password(password):
            login_user(admin)
            return redirect(url_for("admin_dashboard"))
        flash("Invalid admin credentials.", "danger")
    return render_template("admin/login.html")


@app.route("/admin")
@admin_required
def admin_dashboard():
    total_users = User.query.filter_by(role="user").count()
    total_events = Event.query.count()
    total_bookings = Booking.query.filter_by(status="confirmed").count()
    total_revenue = db.session.query(db.func.sum(Booking.total_amount)).filter_by(status="confirmed").scalar() or 0
    upcoming_events = Event.query.filter(Event.date >= date.today()).count()

    recent_bookings = Booking.query.order_by(Booking.booking_date.desc()).limit(8).all()

    # revenue by month (last 6 buckets by creation order, simplified)
    bookings = Booking.query.filter_by(status="confirmed").all()
    month_revenue = {}
    for b in bookings:
        key = b.booking_date.strftime("%b")
        month_revenue[key] = month_revenue.get(key, 0) + b.total_amount
    if not month_revenue:
        month_revenue = {"No data": 0}

    cat_counts = {}
    for e in Event.query.all():
        cat_counts[e.category] = cat_counts.get(e.category, 0) + 1

    return render_template("admin/dashboard.html", total_users=total_users, total_events=total_events,
                            total_bookings=total_bookings, total_revenue=total_revenue,
                            upcoming_events=upcoming_events, recent_bookings=recent_bookings,
                            month_labels=list(month_revenue.keys()), month_values=list(month_revenue.values()),
                            cat_labels=list(cat_counts.keys()), cat_values=list(cat_counts.values()))


@app.route("/admin/events")
@admin_required
def admin_events():
    q = request.args.get("q", "").strip()
    query = Event.query
    if q:
        query = query.filter(Event.title.ilike(f"%{q}%"))
    events_list = query.order_by(Event.created_at.desc()).all()
    return render_template("admin/events.html", events=events_list, q=q)


def parse_access_datetime(raw):
    if not raw:
        return None
    try:
        return datetime.strptime(raw, "%Y-%m-%dT%H:%M")
    except ValueError:
        return None


@app.route("/admin/events/new", methods=["GET", "POST"])
@admin_required
def admin_event_new():
    categories = Category.query.all()
    if request.method == "POST":
        event_type = request.form.get("event_type", "physical")
        if event_type not in ("physical", "online", "hybrid"):
            event_type = "physical"
        organizer_id_raw = request.form.get("organizer_user_id")
        organizer_user_id = None
        if organizer_id_raw:
            candidate = User.query.filter_by(id=int(organizer_id_raw), role="organizer").first()
            organizer_user_id = candidate.id if candidate else None
        event = Event(
            title=request.form["title"],
            description=request.form["description"],
            category_id=int(request.form["category_id"]),
            location=request.form["location"],
            venue=request.form["venue"],
            date=datetime.strptime(request.form["date"], "%Y-%m-%d").date(),
            time=request.form["time"],
            organizer=request.form["organizer"],
            image=request.form["image"] or "https://images.unsplash.com/photo-1492684223066-81342ee5ff30",
            total_seats=int(request.form["total_seats"]),
            is_trending=bool(request.form.get("is_trending")),
            event_type=event_type,
            streaming_url=request.form.get("streaming_url", "").strip() or None,
            access_start=parse_access_datetime(request.form.get("access_start")),
            access_end=parse_access_datetime(request.form.get("access_end")),
            organizer_user_id=organizer_user_id,
            is_published=bool(request.form.get("is_published")),
            parking_available=bool(request.form.get("parking_available")),
            parking_price=float(request.form["parking_price"]) if request.form.get("parking_price") else None,
            parking_info=request.form.get("parking_info", "").strip() or None,
        )
        db.session.add(event)
        db.session.flush()

        names = request.form.getlist("ticket_name[]")
        prices = request.form.getlist("ticket_price[]")
        streaming_flags = request.form.getlist("ticket_streaming[]")
        for i, (n, p) in enumerate(zip(names, prices)):
            if n and p:
                is_stream = event_type != "physical" and i < len(streaming_flags) and streaming_flags[i] == "1"
                db.session.add(TicketType(event_id=event.id, name=n, price=float(p), is_streaming=is_stream))

        db.session.commit()
        flash("Event created successfully.", "success")
        return redirect(url_for("admin_events"))

    return render_template("admin/event_form.html", categories=categories, event=None, organizers=User.query.filter_by(role="organizer").all())


@app.route("/admin/events/<int:event_id>/edit", methods=["GET", "POST"])
@admin_required
def admin_event_edit(event_id):
    event = Event.query.get_or_404(event_id)
    categories = Category.query.all()
    if request.method == "POST":
        event_type = request.form.get("event_type", "physical")
        if event_type not in ("physical", "online", "hybrid"):
            event_type = "physical"
        event.title = request.form["title"]
        event.description = request.form["description"]
        event.category_id = int(request.form["category_id"])
        event.location = request.form["location"]
        event.venue = request.form["venue"]
        event.date = datetime.strptime(request.form["date"], "%Y-%m-%d").date()
        event.time = request.form["time"]
        event.organizer = request.form["organizer"]
        event.image = request.form["image"]
        event.total_seats = int(request.form["total_seats"])
        event.is_trending = bool(request.form.get("is_trending"))
        event.is_published = bool(request.form.get("is_published"))
        event.parking_available = bool(request.form.get("parking_available"))
        event.parking_price = float(request.form["parking_price"]) if request.form.get("parking_price") else None
        event.parking_info = request.form.get("parking_info", "").strip() or None
        event.event_type = event_type
        event.streaming_url = request.form.get("streaming_url", "").strip() or None
        event.access_start = parse_access_datetime(request.form.get("access_start"))
        event.access_end = parse_access_datetime(request.form.get("access_end"))

        organizer_id_raw = request.form.get("organizer_user_id")
        if organizer_id_raw:
            candidate = User.query.filter_by(id=int(organizer_id_raw), role="organizer").first()
            event.organizer_user_id = candidate.id if candidate else None
        else:
            event.organizer_user_id = None

        TicketType.query.filter_by(event_id=event.id).delete()
        names = request.form.getlist("ticket_name[]")
        prices = request.form.getlist("ticket_price[]")
        streaming_flags = request.form.getlist("ticket_streaming[]")
        for i, (n, p) in enumerate(zip(names, prices)):
            if n and p:
                # event_type='physical' forces every ticket back to non-streaming, even if a
                # stale streaming flag came through from the form — admin flipping the event
                # to physical is a deliberate "turn off online access" action.
                is_stream = event_type != "physical" and i < len(streaming_flags) and streaming_flags[i] == "1"
                db.session.add(TicketType(event_id=event.id, name=n, price=float(p), is_streaming=is_stream))

        db.session.commit()
        flash("Event updated successfully.", "success")
        return redirect(url_for("admin_events"))

    return render_template("admin/event_form.html", categories=categories, event=event, organizers=User.query.filter_by(role="organizer").all())


@app.route("/admin/events/<int:event_id>/disable-streaming", methods=["POST"])
@admin_required
def admin_event_disable_streaming(event_id):
    """Quick one-click revoke: switches the event back to physical-only. Ticket types and
    past bookings are left untouched (nothing is deleted) — stream_access simply stops
    granting access because event.stream_state becomes NOT_ENABLED."""
    event = Event.query.get_or_404(event_id)
    event.event_type = "physical"
    db.session.commit()
    flash(f"Online access disabled for \"{event.title}\".", "success")
    return redirect(url_for("admin_events"))


@app.route("/admin/events/<int:event_id>/delete", methods=["POST"])
@admin_required
def admin_event_delete(event_id):
    event = Event.query.get_or_404(event_id)
    db.session.delete(event)
    db.session.commit()
    flash("Event deleted.", "info")
    return redirect(url_for("admin_events"))


@app.route("/admin/users")
@admin_required
def admin_users():
    q = request.args.get("q", "").strip()
    query = User.query.filter_by(role="user")
    if q:
        query = query.filter(User.name.ilike(f"%{q}%") | User.email.ilike(f"%{q}%"))
    users_list = query.order_by(User.created_at.desc()).all()
    return render_template("admin/users.html", users=users_list, q=q)


@app.route("/admin/users/<int:user_id>/toggle", methods=["POST"])
@admin_required
def admin_user_toggle(user_id):
    user = User.query.get_or_404(user_id)
    user.status = "disabled" if user.status == "active" else "active"
    db.session.commit()
    return redirect(request.referrer or url_for("admin_users"))


@app.route("/admin/organizers")
@admin_required
def admin_organizers():
    organizers = User.query.filter_by(role="organizer").order_by(User.created_at.desc()).all()
    return render_template("admin/organizers.html", organizers=organizers)


@app.route("/organizers/<username>")
def organizer_public_profile(username):
    organizer = User.query.filter_by(username=username, role="organizer").first_or_404()
    events = Event.query.filter_by(organizer_user_id=organizer.id, is_published=True).order_by(Event.date.desc()).all()
    all_ratings = [r for e in events for r in e.visible_reviews]
    avg_rating = round(sum(r.rating for r in all_ratings) / len(all_ratings), 1) if all_ratings else None
    total_attendance = sum(e.booked_seats for e in events)
    already_following = organizer.is_followed_by(current_user)
    return render_template("organizer_public_profile.html", organizer=organizer, events=events,
                            avg_rating=avg_rating, review_count=len(all_ratings),
                            total_attendance=total_attendance, already_following=already_following)


@app.route("/admin/organizers/<int:user_id>/toggle-verified", methods=["POST"])
@admin_required
def admin_organizer_toggle_verified(user_id):
    organizer = User.query.filter_by(id=user_id, role="organizer").first_or_404()
    organizer.is_verified = not organizer.is_verified
    db.session.commit()
    flash(f"{organizer.name} is now {'Eventify Verified' if organizer.is_verified else 'unverified'}.", "success")
    return redirect(url_for("admin_organizers"))


@app.route("/admin/organizers/new", methods=["POST"])
@admin_required
def admin_organizer_new():
    name = request.form.get("name", "").strip()
    email = request.form.get("email", "").strip().lower()
    password = request.form.get("password", "")
    if not name or not email or not password:
        flash("Name, email and password are all required.", "danger")
        return redirect(url_for("admin_organizers"))
    if User.query.filter_by(email=email).first():
        flash(f"An account with {email} already exists.", "danger")
        return redirect(url_for("admin_organizers"))
    organizer = User(name=name, email=email, role="organizer")
    organizer.set_password(password)
    organizer.username = generate_username(email)
    organizer.referral_code = generate_referral_code()
    db.session.add(organizer)
    db.session.commit()
    flash(f"Organizer account created for {name}.", "success")
    return redirect(url_for("admin_organizers"))


@app.route("/admin/bookings")
@admin_required
def admin_bookings():
    bookings = Booking.query.order_by(Booking.booking_date.desc()).all()
    return render_template("admin/bookings.html", bookings=bookings)


@app.route("/admin/reports")
@admin_required
def admin_reports():
    status_filter = request.args.get("status", "pending")
    comment_reports = CommentReport.query.order_by(CommentReport.created_at.desc()).all()
    review_reports = ReviewReport.query.order_by(ReviewReport.created_at.desc()).all()
    say_hi_reports = SayHi.query.filter_by(status="reported").order_by(SayHi.created_at.desc()).all()
    if status_filter != "all":
        comment_reports = [r for r in comment_reports if r.status == status_filter]
        review_reports = [r for r in review_reports if r.status == status_filter]
    return render_template("admin/reports.html", comment_reports=comment_reports,
                            review_reports=review_reports, say_hi_reports=say_hi_reports,
                            status_filter=status_filter)


@app.route("/admin/reports/comment/<int:report_id>/<action>", methods=["POST"])
@admin_required
def admin_report_comment_action(report_id, action):
    report = CommentReport.query.get_or_404(report_id)
    if action == "resolve":
        report.status = "resolved"
        report.comment.is_deleted = True  # resolving a valid report removes the offending comment
    elif action == "reject":
        report.status = "rejected"
    else:
        abort(400)
    db.session.commit()
    return redirect(url_for("admin_reports"))


@app.route("/admin/reports/review/<int:report_id>/<action>", methods=["POST"])
@admin_required
def admin_report_review_action(report_id, action):
    report = ReviewReport.query.get_or_404(report_id)
    if action == "resolve":
        report.status = "resolved"
        report.review.is_deleted = True
    elif action == "reject":
        report.status = "rejected"
    else:
        abort(400)
    db.session.commit()
    return redirect(url_for("admin_reports"))


@app.route("/admin/coupons")
@admin_required
def admin_coupons():
    coupons = Coupon.query.order_by(Coupon.created_at.desc()).all()
    return render_template("admin/coupons.html", coupons=coupons)


@app.route("/admin/coupons/new", methods=["POST"])
@admin_required
def admin_coupon_new():
    code = request.form.get("code", "").strip()
    discount_type = request.form.get("discount_type", "percent")
    if discount_type not in ("percent", "flat"):
        discount_type = "percent"
    if not code:
        flash("Coupon code is required.", "danger")
        return redirect(url_for("admin_coupons"))
    if Coupon.query.filter(db.func.lower(Coupon.code) == code.lower()).first():
        flash(f"A coupon with code \"{code}\" already exists.", "danger")
        return redirect(url_for("admin_coupons"))
    try:
        coupon = Coupon(
            code=code,
            discount_type=discount_type,
            discount_percent=float(request.form.get("discount_percent") or 0),
            discount_amount=float(request.form["discount_amount"]) if request.form.get("discount_amount") else None,
            min_booking_amount=float(request.form["min_booking_amount"]) if request.form.get("min_booking_amount") else None,
            max_discount_amount=float(request.form["max_discount_amount"]) if request.form.get("max_discount_amount") else None,
            usage_limit=int(request.form["usage_limit"]) if request.form.get("usage_limit") else None,
            expiry_date=datetime.strptime(request.form["expiry_date"], "%Y-%m-%d").date() if request.form.get("expiry_date") else None,
            active=bool(request.form.get("active")),
        )
    except ValueError:
        flash("One of the numeric fields wasn't a valid number.", "danger")
        return redirect(url_for("admin_coupons"))
    if discount_type == "percent" and not (0 < coupon.discount_percent <= 100):
        flash("Percent discount must be between 1 and 100.", "danger")
        return redirect(url_for("admin_coupons"))
    if discount_type == "flat" and not coupon.discount_amount:
        flash("Flat discount needs an amount.", "danger")
        return redirect(url_for("admin_coupons"))
    db.session.add(coupon)
    db.session.commit()
    flash(f"Coupon \"{code}\" created.", "success")
    return redirect(url_for("admin_coupons"))


@app.route("/admin/coupons/<int:coupon_id>/edit", methods=["POST"])
@admin_required
def admin_coupon_edit(coupon_id):
    coupon = Coupon.query.get_or_404(coupon_id)
    discount_type = request.form.get("discount_type", "percent")
    if discount_type not in ("percent", "flat"):
        discount_type = "percent"
    try:
        coupon.discount_type = discount_type
        coupon.discount_percent = float(request.form.get("discount_percent") or 0)
        coupon.discount_amount = float(request.form["discount_amount"]) if request.form.get("discount_amount") else None
        coupon.min_booking_amount = float(request.form["min_booking_amount"]) if request.form.get("min_booking_amount") else None
        coupon.max_discount_amount = float(request.form["max_discount_amount"]) if request.form.get("max_discount_amount") else None
        coupon.usage_limit = int(request.form["usage_limit"]) if request.form.get("usage_limit") else None
        coupon.expiry_date = (datetime.strptime(request.form["expiry_date"], "%Y-%m-%d").date()
                               if request.form.get("expiry_date") else None)
        coupon.active = bool(request.form.get("active"))
    except ValueError:
        flash("One of the numeric fields wasn't a valid number.", "danger")
        return redirect(url_for("admin_coupons"))
    db.session.commit()
    flash(f"Coupon \"{coupon.code}\" updated.", "success")
    return redirect(url_for("admin_coupons"))


@app.route("/admin/coupons/<int:coupon_id>/toggle", methods=["POST"])
@admin_required
def admin_coupon_toggle(coupon_id):
    coupon = Coupon.query.get_or_404(coupon_id)
    coupon.active = not coupon.active
    db.session.commit()
    flash(f"Coupon \"{coupon.code}\" is now {'active' if coupon.active else 'disabled'}.", "success")
    return redirect(url_for("admin_coupons"))


@app.route("/admin/coupons/<int:coupon_id>/delete", methods=["POST"])
@admin_required
def admin_coupon_delete(coupon_id):
    coupon = Coupon.query.get_or_404(coupon_id)
    if coupon.times_used:
        flash(f"Coupon \"{coupon.code}\" has been used {coupon.times_used} time(s) — disable it instead of deleting, to keep booking history accurate.", "warning")
        return redirect(url_for("admin_coupons"))
    db.session.delete(coupon)
    db.session.commit()
    flash("Coupon deleted.", "success")
    return redirect(url_for("admin_coupons"))


@app.route("/admin/comments")
@admin_required
def admin_comments():
    reported_only = request.args.get("reported") == "1"
    query = (EventComment.query.filter_by(is_deleted=False)
             .order_by(EventComment.created_at.desc()))
    all_comments = query.all()
    if reported_only:
        all_comments = [c for c in all_comments if c.report_count > 0]
    return render_template("admin/comments.html", comments=all_comments, reported_only=reported_only)


@app.route("/admin/comments/<int:comment_id>/delete", methods=["POST"])
@admin_required
def admin_comment_delete(comment_id):
    comment = EventComment.query.get_or_404(comment_id)
    comment.is_deleted = True
    db.session.commit()
    flash("Comment removed.", "success")
    return redirect(url_for("admin_comments"))


@app.route("/admin/reviews")
@admin_required
def admin_reviews():
    reported_only = request.args.get("reported") == "1"
    all_reviews = Review.query.filter_by(is_deleted=False).order_by(Review.created_at.desc()).all()
    if reported_only:
        all_reviews = [r for r in all_reviews if r.report_count > 0]
    return render_template("admin/reviews.html", reviews=all_reviews, reported_only=reported_only)


@app.route("/admin/reviews/<int:review_id>/delete", methods=["POST"])
@admin_required
def admin_review_delete(review_id):
    review = Review.query.get_or_404(review_id)
    review.is_deleted = True
    db.session.commit()
    flash("Review removed.", "success")
    return redirect(url_for("admin_reviews"))


@app.route("/admin/analytics")
@admin_required
def admin_analytics():
    events_list = Event.query.all()
    popular = sorted(events_list, key=lambda e: e.booked_seats, reverse=True)[:5]
    cat_counts = {}
    for e in events_list:
        cat_counts[e.category] = cat_counts.get(e.category, 0) + e.booked_seats

    # Only bookings with an actual successful payment count as valid revenue —
    # matches the same definition used for organizer/event online analytics.
    bookings = (Booking.query.join(Payment, Payment.booking_id == Booking.id)
                .filter(Booking.status == "confirmed", Payment.status == "success").all())
    month_revenue = {}
    month_bookings = {}
    for b in bookings:
        key = b.booking_date.strftime("%b")
        month_revenue[key] = month_revenue.get(key, 0) + b.total_amount
        month_bookings[key] = month_bookings.get(key, 0) + 1
    if not month_revenue:
        month_revenue = {"No data": 0}
        month_bookings = {"No data": 0}

    total_revenue = sum(b.total_amount for b in bookings)
    total_online_revenue = sum(e.online_revenue for e in events_list)
    total_online_tickets = sum(e.online_tickets_sold for e in events_list)
    total_online_attendees = len({u.id for e in events_list for u in e.online_attendees})

    event_wise = sorted(
        [{"event": e, "revenue": e.gross_revenue, "online_revenue": e.online_revenue} for e in events_list],
        key=lambda row: row["revenue"], reverse=True)[:10]

    organizer_wise = []
    for org in User.query.filter_by(role="organizer").all():
        org_events = [e for e in events_list if e.organizer_user_id == org.id]
        if not org_events:
            continue
        organizer_wise.append({
            "organizer": org,
            "event_count": len(org_events),
            "revenue": sum(e.gross_revenue for e in org_events),
            "online_revenue": sum(e.online_revenue for e in org_events),
        })
    organizer_wise.sort(key=lambda row: row["revenue"], reverse=True)

    return render_template("admin/analytics.html", popular=popular, cat_labels=list(cat_counts.keys()),
                            cat_values=list(cat_counts.values()), month_labels=list(month_revenue.keys()),
                            month_revenue_values=list(month_revenue.values()),
                            month_booking_values=list(month_bookings.values()),
                            total_revenue=total_revenue, total_bookings=len(bookings),
                            user_count=User.query.filter_by(role="user").count(),
                            total_online_revenue=total_online_revenue,
                            total_online_tickets=total_online_tickets,
                            total_online_attendees=total_online_attendees,
                            event_wise=event_wise, organizer_wise=organizer_wise)


@app.route("/admin/logout")
@login_required
def admin_logout():
    logout_user()
    return redirect(url_for("admin_login"))


# ---------- organizer ----------

@app.route("/organizer/register", methods=["GET", "POST"])
def organizer_register():
    if current_user.is_authenticated:
        if current_user.role == "organizer":
            return redirect(url_for("organizer_dashboard"))
        return redirect(url_for("home"))

    if request.method == "POST":
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip().lower()
        phone = request.form.get("phone", "").strip()
        password = request.form.get("password", "")
        confirm = request.form.get("confirm_password", "")

        error = None

        if not name or not email or not password:
            error = "Please fill in all required fields."
        elif password != confirm:
            error = "Passwords do not match."
        elif len(password) < 6:
            error = "Password must be at least 6 characters."
        elif User.query.filter_by(email=email).first():
            error = "An account with this email already exists."

        if error:
            flash(error, "danger")
            return render_template(
                "organizer/register.html",
                form=request.form
            )

        organizer = User(
            name=name,
            email=email,
            phone=phone,
            role="organizer",
            status="active"
        )

        organizer.set_password(password)
        organizer.username = generate_username(email)
        organizer.referral_code = generate_referral_code()

        db.session.add(organizer)
        db.session.commit()

        flash(
            "Organizer account created successfully. Please log in.",
            "success"
        )

        return redirect(url_for("organizer_login"))

    return render_template(
        "organizer/register.html",
        form={}
    )

@app.route("/organizer/login", methods=["GET", "POST"])
def organizer_login():
    if current_user.is_authenticated and current_user.role == "organizer":
        return redirect(url_for("organizer_dashboard"))

    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")

        organizer = User.query.filter_by(
            email=email,
            role="organizer"
        ).first()

        if organizer and organizer.check_password(password):

            if organizer.status == "disabled":
                flash(
                    "Your organizer account has been disabled. Contact Eventify support.",
                    "danger"
                )
                return render_template("organizer/login.html")

            login_user(organizer)
            return redirect(url_for("organizer_dashboard"))

        flash("Invalid organizer credentials.", "danger")

    return render_template("organizer/login.html")

@app.route("/organizer/logout")
@login_required
def organizer_logout():
    logout_user()
    return redirect(url_for("organizer_login"))


@app.route("/organizer")
@organizer_required
def organizer_dashboard():
    events = Event.query.filter_by(organizer_user_id=current_user.id).order_by(Event.date.desc()).all()
    event_ids = [e.id for e in events]

    total_events = len(events)
    published_count = sum(1 for e in events if e.is_published)
    draft_count = total_events - published_count
    upcoming_count = sum(1 for e in events if e.date >= date.today())

    all_bookings = (Booking.query.filter(Booking.event_id.in_(event_ids)).all() if event_ids else [])
    total_bookings = len(all_bookings)

    total_online_tickets = sum(e.online_tickets_sold for e in events)
    total_physical_tickets = sum(e.physical_tickets_sold for e in events)
    total_tickets_sold = total_online_tickets + total_physical_tickets
    total_online_revenue = sum(e.online_revenue for e in events)
    total_gross_revenue = sum(e.gross_revenue for e in events)

    recent_bookings = (Booking.query.filter(Booking.event_id.in_(event_ids))
                        .order_by(Booking.booking_date.desc()).limit(8).all() if event_ids else [])

    return render_template("organizer/dashboard.html", events=events,
                            total_events=total_events, published_count=published_count,
                            draft_count=draft_count, upcoming_count=upcoming_count,
                            total_bookings=total_bookings, total_tickets_sold=total_tickets_sold,
                            total_online_tickets=total_online_tickets,
                            total_physical_tickets=total_physical_tickets,
                            total_online_revenue=total_online_revenue,
                            total_gross_revenue=total_gross_revenue,
                            recent_bookings=recent_bookings)


@app.route("/organizer/events/new", methods=["GET", "POST"])
@organizer_required
def organizer_event_new():
    categories = Category.query.all()
    if request.method == "POST":
        event_type = request.form.get("event_type", "physical")
        if event_type not in ("physical", "online", "hybrid"):
            event_type = "physical"
        event = Event(
            title=request.form["title"],
            description=request.form["description"],
            category_id=int(request.form["category_id"]),
            location=request.form["location"],
            venue=request.form["venue"],
            date=datetime.strptime(request.form["date"], "%Y-%m-%d").date(),
            time=request.form["time"],
            organizer=request.form.get("organizer", "").strip() or current_user.name,
            image=request.form["image"] or "https://images.unsplash.com/photo-1492684223066-81342ee5ff30",
            total_seats=int(request.form["total_seats"]),
            event_type=event_type,
            streaming_url=request.form.get("streaming_url", "").strip() or None,
            access_start=parse_access_datetime(request.form.get("access_start")),
            access_end=parse_access_datetime(request.form.get("access_end")),
            # Ownership is NEVER taken from the submitted form — an organizer can only
            # ever create events attributed to themselves.
            organizer_user_id=current_user.id,
            is_published=False,  # starts as a draft; organizer publishes explicitly
            parking_available=bool(request.form.get("parking_available")),
            parking_price=float(request.form["parking_price"]) if request.form.get("parking_price") else None,
            parking_info=request.form.get("parking_info", "").strip() or None,
        )
        db.session.add(event)
        db.session.flush()

        names = request.form.getlist("ticket_name[]")
        prices = request.form.getlist("ticket_price[]")
        streaming_flags = request.form.getlist("ticket_streaming[]")
        for i, (n, p) in enumerate(zip(names, prices)):
            if n and p:
                is_stream = event_type != "physical" and i < len(streaming_flags) and streaming_flags[i] == "1"
                db.session.add(TicketType(event_id=event.id, name=n, price=float(p), is_streaming=is_stream))

        db.session.commit()
        flash("Event created as a draft. Publish it when you're ready for customers to see it.", "success")
        return redirect(url_for("organizer_event_detail", event_id=event.id))

    return render_template("organizer/event_form.html", categories=categories, event=None)


@app.route("/organizer/events/<int:event_id>/edit", methods=["GET", "POST"])
@organizer_required
def organizer_event_edit(event_id):
    # Ownership check happens here, at the query level, on every request — an organizer
    # cannot reach another organizer's event no matter what ID is typed into the URL.
    event = Event.query.filter_by(id=event_id, organizer_user_id=current_user.id).first_or_404()
    categories = Category.query.all()
    if request.method == "POST":
        event_type = request.form.get("event_type", "physical")
        if event_type not in ("physical", "online", "hybrid"):
            event_type = "physical"
        event.title = request.form["title"]
        event.description = request.form["description"]
        event.category_id = int(request.form["category_id"])
        event.location = request.form["location"]
        event.venue = request.form["venue"]
        event.date = datetime.strptime(request.form["date"], "%Y-%m-%d").date()
        event.time = request.form["time"]
        event.organizer = request.form.get("organizer", "").strip() or event.organizer
        event.image = request.form["image"]
        event.total_seats = int(request.form["total_seats"])
        event.parking_available = bool(request.form.get("parking_available"))
        event.parking_price = float(request.form["parking_price"]) if request.form.get("parking_price") else None
        event.parking_info = request.form.get("parking_info", "").strip() or None
        event.event_type = event_type
        event.streaming_url = request.form.get("streaming_url", "").strip() or None
        event.access_start = parse_access_datetime(request.form.get("access_start"))
        event.access_end = parse_access_datetime(request.form.get("access_end"))
        # organizer_user_id is deliberately never touched here — an organizer editing
        # their own event can't reassign it to someone else; only admin can do that.

        TicketType.query.filter_by(event_id=event.id).delete()
        names = request.form.getlist("ticket_name[]")
        prices = request.form.getlist("ticket_price[]")
        streaming_flags = request.form.getlist("ticket_streaming[]")
        for i, (n, p) in enumerate(zip(names, prices)):
            if n and p:
                is_stream = event_type != "physical" and i < len(streaming_flags) and streaming_flags[i] == "1"
                db.session.add(TicketType(event_id=event.id, name=n, price=float(p), is_streaming=is_stream))

        db.session.commit()
        flash("Event updated.", "success")
        return redirect(url_for("organizer_event_detail", event_id=event.id))

    return render_template("organizer/event_form.html", categories=categories, event=event)


@app.route("/organizer/events/<int:event_id>/publish", methods=["POST"])
@organizer_required
def organizer_event_publish_toggle(event_id):
    event = Event.query.filter_by(id=event_id, organizer_user_id=current_user.id).first_or_404()
    event.is_published = not event.is_published
    db.session.commit()
    flash(f"\"{event.title}\" is now {'published' if event.is_published else 'a draft'}.", "success")
    return redirect(request.referrer or url_for("organizer_dashboard"))


@app.route("/organizer/events/<int:event_id>/livestream", methods=["GET", "POST"])
@organizer_required
def organizer_livestream(event_id):
    """Scoped livestream management for the organizer's own event only — kept as a
    focused sub-page alongside the full event editor above, since streaming setup is
    a distinct, frequently-revisited task (updating a URL closer to the event date,
    adjusting the access window) separate from editing the core listing."""
    event = Event.query.filter_by(id=event_id, organizer_user_id=current_user.id).first_or_404()
    if request.method == "POST":
        event_type = request.form.get("event_type", "physical")
        if event_type not in ("physical", "online", "hybrid"):
            event_type = "physical"
        event.event_type = event_type
        event.streaming_url = request.form.get("streaming_url", "").strip() or None
        event.access_start = parse_access_datetime(request.form.get("access_start"))
        event.access_end = parse_access_datetime(request.form.get("access_end"))
        if event_type == "physical":
            for tt in event.ticket_types:
                tt.is_streaming = False
        else:
            streaming_ids = set(request.form.getlist("streaming_ticket_ids"))
            for tt in event.ticket_types:
                tt.is_streaming = str(tt.id) in streaming_ids
        db.session.commit()
        flash("Livestream settings updated.", "success")
        return redirect(url_for("organizer_livestream", event_id=event.id))
    return render_template("organizer/livestream.html", event=event)


@app.route("/organizer/events/<int:event_id>")
@organizer_required
def organizer_event_detail(event_id):
    event = Event.query.filter_by(id=event_id, organizer_user_id=current_user.id).first_or_404()
    all_bookings = Booking.query.filter_by(event_id=event.id).order_by(Booking.booking_date.desc()).all()
    online_bookings = event.online_bookings
    online_attendees = event.online_attendees
    return render_template("organizer/event_detail.html", event=event, all_bookings=all_bookings,
                            online_bookings=online_bookings, online_attendees=online_attendees)


# ---------- errors ----------

@app.errorhandler(404)
def not_found(e):
    return render_template("errors/404.html"), 404


@app.errorhandler(403)
def forbidden(e):
    return render_template("errors/403.html"), 403


if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5000)
