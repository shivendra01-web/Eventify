from datetime import datetime, timezone
from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash

db = SQLAlchemy()


class User(UserMixin, db.Model):
    __tablename__ = "users"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    phone = db.Column(db.String(20))
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(20), default="user")  # user | admin
    status = db.Column(db.String(20), default="active")  # active | disabled
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    bookings = db.relationship("Booking", back_populates="user", foreign_keys="Booking.user_id", lazy=True)
    wishlist_items = db.relationship("Wishlist", backref="user", lazy=True)

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    @property
    def is_admin(self):
        return self.role == "admin"


class Category(db.Model):
    __tablename__ = "categories"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(60), unique=True, nullable=False)
    icon = db.Column(db.String(60), default="fa-star")

    events = db.relationship("Event", backref="category_ref", lazy=True)


class Event(db.Model):
    __tablename__ = "events"
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(160), nullable=False)
    description = db.Column(db.Text, nullable=False)
    category_id = db.Column(db.Integer, db.ForeignKey("categories.id"))
    location = db.Column(db.String(120), nullable=False)
    venue = db.Column(db.String(160), nullable=False)
    date = db.Column(db.Date, nullable=False)
    time = db.Column(db.String(20), nullable=False)
    organizer = db.Column(db.String(120), nullable=False)
    image = db.Column(db.String(300), nullable=False)
    total_seats = db.Column(db.Integer, default=100)
    booked_seats = db.Column(db.Integer, default=0)
    is_trending = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    # Stage 2b: future live-stream URL, set by admin once available. Nullable — never
    # exposed to attendees until their payment/entitlement is verified server-side.
    streaming_url = db.Column(db.String(400))

    ticket_types = db.relationship("TicketType", backref="event", lazy=True, cascade="all, delete-orphan")
    bookings = db.relationship("Booking", backref="event", lazy=True)

    @property
    def category(self):
        return self.category_ref.name if self.category_ref else "General"

    @property
    def available_seats(self):
        return max(self.total_seats - self.booked_seats, 0)

    @property
    def min_price(self):
        physical = [t.price for t in self.ticket_types if not t.is_streaming]
        prices = physical or [t.price for t in self.ticket_types]
        return min(prices) if prices else 0

    @property
    def is_sold_out(self):
        return self.available_seats <= 0

    @property
    def is_new(self):
        created = self.created_at
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - created).days <= 3


class TicketType(db.Model):
    __tablename__ = "ticket_types"
    id = db.Column(db.Integer, primary_key=True)
    event_id = db.Column(db.Integer, db.ForeignKey("events.id"), nullable=False)
    name = db.Column(db.String(60), nullable=False)  # Regular / VIP / Premium / Live Streaming
    price = db.Column(db.Float, nullable=False)
    is_streaming = db.Column(db.Boolean, default=False)


class Coupon(db.Model):
    __tablename__ = "coupons"
    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(40), unique=True, nullable=False)
    discount_percent = db.Column(db.Float, nullable=False)  # e.g. 50 = 50% off
    active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))


class Booking(db.Model):
    __tablename__ = "bookings"
    id = db.Column(db.Integer, primary_key=True)
    booking_ref = db.Column(db.String(20), unique=True, nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    event_id = db.Column(db.Integer, db.ForeignKey("events.id"), nullable=False)
    customer_name = db.Column(db.String(120))
    customer_email = db.Column(db.String(120))
    customer_phone = db.Column(db.String(20))
    total_amount = db.Column(db.Float, nullable=False)
    status = db.Column(db.String(20), default="confirmed")  # confirmed | cancelled
    booking_date = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    # Stage 2 additions — all nullable so existing bookings remain valid
    coupon_code = db.Column(db.String(40))
    discount_amount = db.Column(db.Float, default=0)
    meal_choice = db.Column(db.String(40))
    # Stage 2b: tracks the ORIGINAL purchaser permanently, even after a ticket transfer
    # changes user_id to the new owner. Never updated after first being set.
    original_user_id = db.Column(db.Integer, db.ForeignKey("users.id"))

    items = db.relationship("BookingItem", backref="booking", lazy=True, cascade="all, delete-orphan")
    payment = db.relationship("Payment", backref="booking", uselist=False, cascade="all, delete-orphan")
    transfers = db.relationship("TicketTransfer", backref="booking", lazy=True,
                                 cascade="all, delete-orphan", order_by="TicketTransfer.created_at.desc()")
    user = db.relationship("User", back_populates="bookings", foreign_keys=[user_id])
    original_user = db.relationship("User", foreign_keys=[original_user_id])

    @property
    def total_tickets(self):
        return sum(i.quantity for i in self.items)

    @property
    def has_streaming_item(self):
        return any(i.is_streaming for i in self.items)

    @property
    def pending_transfer(self):
        return next((t for t in self.transfers if t.status == "pending"), None)


class BookingItem(db.Model):
    __tablename__ = "booking_items"
    id = db.Column(db.Integer, primary_key=True)
    booking_id = db.Column(db.Integer, db.ForeignKey("bookings.id"), nullable=False)
    ticket_type = db.Column(db.String(60), nullable=False)
    price = db.Column(db.Float, nullable=False)
    quantity = db.Column(db.Integer, nullable=False)
    is_streaming = db.Column(db.Boolean, default=False)


class TicketTransfer(db.Model):
    """Secure ticket-transfer request + audit trail. Rows are never deleted —
    they form the permanent audit history of ownership changes for a booking."""
    __tablename__ = "ticket_transfers"
    id = db.Column(db.Integer, primary_key=True)
    booking_id = db.Column(db.Integer, db.ForeignKey("bookings.id"), nullable=False)
    from_user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    to_user_id = db.Column(db.Integer, db.ForeignKey("users.id"))  # filled in once accepted
    to_email = db.Column(db.String(120), nullable=False)
    to_phone = db.Column(db.String(20), nullable=False)
    token = db.Column(db.String(64), unique=True, nullable=False)
    status = db.Column(db.String(20), default="pending")  # pending | accepted | rejected | cancelled | expired
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    responded_at = db.Column(db.DateTime)

    from_user = db.relationship("User", foreign_keys=[from_user_id])
    to_user = db.relationship("User", foreign_keys=[to_user_id])


class Wishlist(db.Model):
    __tablename__ = "wishlist"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    event_id = db.Column(db.Integer, db.ForeignKey("events.id"), nullable=False)

    event = db.relationship("Event")


class Payment(db.Model):
    __tablename__ = "payments"
    id = db.Column(db.Integer, primary_key=True)
    booking_id = db.Column(db.Integer, db.ForeignKey("bookings.id"), nullable=False)
    payment_method = db.Column(db.String(30), nullable=False)
    transaction_id = db.Column(db.String(40), nullable=False)
    amount = db.Column(db.Float, nullable=False)
    status = db.Column(db.String(20), default="success")
    payment_date = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    # Razorpay tracking (nullable so existing rows / non-Razorpay methods stay valid)
    razorpay_order_id = db.Column(db.String(80))
    razorpay_payment_id = db.Column(db.String(80))
    razorpay_signature = db.Column(db.String(255))
