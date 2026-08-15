import os
import io
import base64
import random
import string
import secrets
from datetime import datetime, date, timezone
from functools import wraps

from dotenv import load_dotenv
from flask import (Flask, render_template, redirect, url_for, request,
                    flash, jsonify, abort, session)
from flask_login import (LoginManager, login_user, logout_user, login_required,
                          current_user)
import qrcode
import razorpay

from models import (db, User, Category, Event, TicketType, Booking, BookingItem,
                     Wishlist, Payment, Coupon, TicketTransfer)

# Loads variables from a local .env file if present (for local development).
# On Render (or any host where the vars are set in the environment already),
# this simply finds no .env file and does nothing — it never overrides
# real environment variables that are already set.
load_dotenv()

BASE_DIR = os.path.abspath(os.path.dirname(__file__))

app = Flask(__name__)
app.config["SECRET_KEY"] = "eventify-dev-secret-key-change-in-production"
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

        # Auto-generate a "Live Streaming" ticket type (~10% of the event's cheapest physical
        # ticket, computed AFTER the price cap above) for any event that doesn't already have
        # one. Idempotent — skipped once present. Skipped entirely for ULLAS (VVIP-only) and
        # for very low-price demo tickets (<₹50) where a streaming tier wouldn't be meaningful.
        for event in Event.query.all():
            has_streaming = any(t.is_streaming for t in event.ticket_types)
            physical_tickets = [t for t in event.ticket_types if not t.is_streaming]
            if has_streaming or not physical_tickets:
                continue
            if event.title == "ULLAS":
                continue  # VVIP-only flagship event — no streaming tier
            base_price = min(t.price for t in physical_tickets)
            if base_price < 50:
                continue  # ticket too cheap for a separate streaming tier to make sense
            stream_price = max(round(base_price * 0.10, -1), 10)  # ~10%, nearest ₹10, min ₹10
            db.session.add(TicketType(event_id=event.id, name="Live Streaming",
                                       price=stream_price, is_streaming=True))
        db.session.commit()


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
            discount = round(subtotal * (coupon.discount_percent / 100), 2)
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
    return {
        "current_year": datetime.now(timezone.utc).year,
        "celebrate_login": celebrate_login,
        "todays_bookings": todays_bookings,
        "today": date.today(),
    }


# ---------- public pages ----------

@app.route("/")
def home():
    categories = Category.query.all()
    featured = Event.query.order_by(Event.created_at.desc()).limit(6).all()
    trending = Event.query.filter_by(is_trending=True).limit(4).all()
    upcoming = Event.query.filter(Event.date >= date.today()).order_by(Event.date.asc()).limit(6).all()
    ullas = Event.query.filter_by(title="ULLAS").first()
    cities = [row[0] for row in db.session.query(Event.location).distinct().order_by(Event.location).all()]
    stats = {
        "users": User.query.filter_by(role="user").count() + 9850,
        "events": Event.query.count() + 470,
        "tickets": BookingItem.query.count() * 3 + 24500,
        "cities": db.session.query(Event.location).distinct().count() + 42,
    }
    return render_template("index.html", categories=categories, featured=featured,
                            trending=trending, upcoming=upcoming, stats=stats,
                            ullas=ullas, cities=cities)


@app.route("/events")
def events():
    q = request.args.get("q", "").strip()
    category = request.args.get("category", "")
    location = request.args.get("location", "")
    sort = request.args.get("sort", "newest")
    max_price = request.args.get("max_price", "").strip()
    date_filter = request.args.get("date", "").strip()

    query = Event.query
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
    locations = sorted({e.location for e in Event.query.all()})
    return render_template("events.html", events=all_events, categories=categories,
                            locations=locations, q=q, category=category,
                            location=location, sort=sort, max_price=max_price, date_filter=date_filter)


@app.route("/events/<int:event_id>")
def event_detail(event_id):
    event = Event.query.get_or_404(event_id)
    related = Event.query.filter(Event.category_id == event.category_id,
                                  Event.id != event.id).limit(3).all()
    in_wishlist = False
    if current_user.is_authenticated:
        in_wishlist = Wishlist.query.filter_by(user_id=current_user.id, event_id=event.id).first() is not None
    return render_template("event_detail.html", event=event, related=related, in_wishlist=in_wishlist)


# ---------- auth ----------

@app.route("/register", methods=["GET", "POST"])
def register():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard"))
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
            return render_template("register.html", form=request.form)

        user = User(name=name, email=email, phone=phone)
        user.set_password(password)
        db.session.add(user)
        db.session.commit()
        flash("Account created successfully. Please log in.", "success")
        return redirect(url_for("login"))

    return render_template("register.html", form={})


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
    return render_template("dashboard.html", bookings=bookings[:5], upcoming=upcoming,
                            total_bookings=len(bookings), wishlist_count=wishlist_count,
                            tickets_used=tickets_used, upcoming_count=len(upcoming))


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
    if request.method == "POST":
        current_user.name = request.form.get("name", current_user.name).strip()
        current_user.phone = request.form.get("phone", current_user.phone).strip()
        db.session.commit()
        flash("Profile updated successfully.", "success")
        return redirect(url_for("profile"))
    return render_template("profile.html")


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

    pending["coupon_code"] = coupon.code
    session["pending_booking"] = pending

    lines = _pending_lines(event, pending)
    pricing = compute_pricing(lines, coupon.code)
    return jsonify({"ok": True, "message": f"Coupon '{coupon.code}' applied — {coupon.discount_percent:.0f}% OFF!",
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
    booking includes a streaming entitlement. The stream URL itself is never rendered
    to anyone who doesn't pass all four checks."""
    booking = Booking.query.get_or_404(booking_id)
    if booking.user_id != current_user.id and not current_user.is_admin:
        abort(403)
    if booking.status != "confirmed" or not booking.payment or booking.payment.status != "success":
        abort(403)
    if not booking.has_streaming_item:
        abort(404)
    return render_template("stream_access.html", booking=booking, event=booking.event)


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


@app.route("/admin/events/new", methods=["GET", "POST"])
@admin_required
def admin_event_new():
    categories = Category.query.all()
    if request.method == "POST":
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
        )
        db.session.add(event)
        db.session.flush()

        names = request.form.getlist("ticket_name[]")
        prices = request.form.getlist("ticket_price[]")
        for n, p in zip(names, prices):
            if n and p:
                db.session.add(TicketType(event_id=event.id, name=n, price=float(p)))

        db.session.commit()
        flash("Event created successfully.", "success")
        return redirect(url_for("admin_events"))

    return render_template("admin/event_form.html", categories=categories, event=None)


@app.route("/admin/events/<int:event_id>/edit", methods=["GET", "POST"])
@admin_required
def admin_event_edit(event_id):
    event = Event.query.get_or_404(event_id)
    categories = Category.query.all()
    if request.method == "POST":
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

        TicketType.query.filter_by(event_id=event.id).delete()
        names = request.form.getlist("ticket_name[]")
        prices = request.form.getlist("ticket_price[]")
        for n, p in zip(names, prices):
            if n and p:
                db.session.add(TicketType(event_id=event.id, name=n, price=float(p)))

        db.session.commit()
        flash("Event updated successfully.", "success")
        return redirect(url_for("admin_events"))

    return render_template("admin/event_form.html", categories=categories, event=event)


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
    return redirect(url_for("admin_users"))


@app.route("/admin/bookings")
@admin_required
def admin_bookings():
    bookings = Booking.query.order_by(Booking.booking_date.desc()).all()
    return render_template("admin/bookings.html", bookings=bookings)


@app.route("/admin/analytics")
@admin_required
def admin_analytics():
    events_list = Event.query.all()
    popular = sorted(events_list, key=lambda e: e.booked_seats, reverse=True)[:5]
    cat_counts = {}
    for e in events_list:
        cat_counts[e.category] = cat_counts.get(e.category, 0) + e.booked_seats

    bookings = Booking.query.filter_by(status="confirmed").all()
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
    return render_template("admin/analytics.html", popular=popular, cat_labels=list(cat_counts.keys()),
                            cat_values=list(cat_counts.values()), month_labels=list(month_revenue.keys()),
                            month_revenue_values=list(month_revenue.values()),
                            month_booking_values=list(month_bookings.values()),
                            total_revenue=total_revenue, total_bookings=len(bookings),
                            user_count=User.query.filter_by(role="user").count())


@app.route("/admin/logout")
@login_required
def admin_logout():
    logout_user()
    return redirect(url_for("admin_login"))


# ---------- errors ----------

@app.errorhandler(404)
def not_found(e):
    return render_template("errors/404.html"), 404


@app.errorhandler(403)
def forbidden(e):
    return render_template("errors/403.html"), 403


if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5000)
