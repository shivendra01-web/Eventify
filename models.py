from datetime import datetime, date, timezone
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

    # Phase 2 — Eventify Connect profile fields. All nullable/defaulted so
    # existing users (created before this feature) remain fully valid.
    username = db.Column(db.String(30), unique=True)
    bio = db.Column(db.String(280))
    city = db.Column(db.String(60))
    profile_visibility = db.Column(db.String(20), default="public")  # public | followers | private
    allow_follow = db.Column(db.Boolean, default=True)
    attendance_visibility = db.Column(db.String(20), default="public")  # public | followers | private
    allow_say_hi = db.Column(db.String(20), default="shared_events")  # yes | shared_events | no

    # Phase 7 — gamification, referrals, Eventify+ (see bottom of file for related models).
    # Points are tracked via a PointsTransaction ledger for auditability; this column is a
    # cached running total kept in sync at award-time, not the source of truth.
    points = db.Column(db.Integer, default=0)
    referral_code = db.Column(db.String(12), unique=True)
    # Eventify+ is UI/architecture only in this build — no real billing is wired up, per the
    # original spec's own instruction not to fake paid membership. Admin toggles this manually.
    is_plus = db.Column(db.Boolean, default=False)
    # "Eventify Verified" — admin-controlled only (see admin_organizers routes). Meaningful
    # for organizer accounts; harmless/no-op if ever set on a non-organizer.
    is_verified = db.Column(db.Boolean, default=False)

    bookings = db.relationship("Booking", back_populates="user", foreign_keys="Booking.user_id", lazy=True)
    wishlist_items = db.relationship("Wishlist", backref="user", lazy=True)
    interests = db.relationship("Interest", secondary="user_interests", backref="users", lazy=True)

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    @property
    def is_admin(self):
        return self.role == "admin"

    @property
    def follower_count(self):
        return Follow.query.filter_by(followed_id=self.id).count()

    @property
    def following_count(self):
        return Follow.query.filter_by(follower_id=self.id).count()

    def is_followed_by(self, other_user):
        if not other_user or not other_user.is_authenticated:
            return False
        return Follow.query.filter_by(follower_id=other_user.id, followed_id=self.id).first() is not None

    def is_visible_to(self, viewer):
        """Access check for a public profile page. Private profiles are
        visible only to the owner; followers-only profiles additionally
        require the viewer to already follow this user."""
        if viewer and viewer.is_authenticated and viewer.id == self.id:
            return True
        if self.profile_visibility == "public":
            return True
        if self.profile_visibility == "followers":
            return bool(viewer and viewer.is_authenticated and self.is_followed_by(viewer))
        return False  # private

    def attendance_visible_to(self, viewer):
        """Same gating logic as is_visible_to, but for whether this user's
        event attendance ('X is going') can be shown to a given viewer."""
        if viewer and viewer.is_authenticated and viewer.id == self.id:
            return True
        if self.attendance_visibility == "public":
            return True
        if self.attendance_visibility == "followers":
            return bool(viewer and viewer.is_authenticated and self.is_followed_by(viewer))
        return False  # private


class Interest(db.Model):
    __tablename__ = "interests"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(40), unique=True, nullable=False)
    emoji = db.Column(db.String(8), default="✨")


user_interests = db.Table(
    "user_interests",
    db.Column("user_id", db.Integer, db.ForeignKey("users.id"), primary_key=True),
    db.Column("interest_id", db.Integer, db.ForeignKey("interests.id"), primary_key=True),
)


class Follow(db.Model):
    """One row per follow relationship. follower_id follows followed_id."""
    __tablename__ = "follows"
    id = db.Column(db.Integer, primary_key=True)
    follower_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    followed_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    __table_args__ = (db.UniqueConstraint("follower_id", "followed_id", name="uq_follow_pair"),)

    follower = db.relationship("User", foreign_keys=[follower_id])
    followed = db.relationship("User", foreign_keys=[followed_id])


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
    # Phase 4: organizer-configurable online/livestream setup. Defaults preserve current
    # behavior for every event that existed before this migration (see run_migrations).
    event_type = db.Column(db.String(20), default="physical")  # physical | online | hybrid
    access_start = db.Column(db.DateTime)  # when the stream becomes joinable; optional
    access_end = db.Column(db.DateTime)    # when access closes; optional
    # Phase 5: optional link to a User account (role='organizer') that manages this event's
    # livestream settings. Nullable — events created before this feature, or events an admin
    # never assigns, are simply unmanaged by any organizer account; the free-text `organizer`
    # field above still displays regardless.
    organizer_user_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    organizer_account = db.relationship("User", foreign_keys=[organizer_user_id], backref="organizer_events")
    # Phase 5: lets an organizer stage an event before it's visible to customers.
    # Defaults True so every event that existed before this column, and every event
    # admin creates directly, behaves exactly as before (immediately public).
    is_published = db.Column(db.Boolean, default=True)
    # Phase 8: parking info — purely informational, no reservation/payment behind it.
    parking_available = db.Column(db.Boolean, default=False)
    parking_price = db.Column(db.Float)
    parking_info = db.Column(db.String(300))

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

    @property
    def has_streaming_ticket(self):
        return any(t.is_streaming for t in self.ticket_types)

    def _valid_online_booking_query(self):
        """A 'valid' online sale = booking is confirmed (not cancelled), has a
        successful payment record, and includes at least one streaming line item.
        Refunded/cancelled/failed/pending payments are excluded by construction."""
        return (Booking.query.join(BookingItem, BookingItem.booking_id == Booking.id)
                .join(Payment, Payment.booking_id == Booking.id)
                .filter(Booking.event_id == self.id, Booking.status == "confirmed",
                        Payment.status == "success", BookingItem.is_streaming.is_(True)))

    @property
    def online_tickets_sold(self):
        return (db.session.query(db.func.coalesce(db.func.sum(BookingItem.quantity), 0))
                .join(Booking, Booking.id == BookingItem.booking_id)
                .join(Payment, Payment.booking_id == Booking.id)
                .filter(Booking.event_id == self.id, Booking.status == "confirmed",
                        Payment.status == "success", BookingItem.is_streaming.is_(True)).scalar())

    @property
    def online_revenue(self):
        return (db.session.query(db.func.coalesce(db.func.sum(BookingItem.price * BookingItem.quantity), 0))
                .join(Booking, Booking.id == BookingItem.booking_id)
                .join(Payment, Payment.booking_id == Booking.id)
                .filter(Booking.event_id == self.id, Booking.status == "confirmed",
                        Payment.status == "success", BookingItem.is_streaming.is_(True)).scalar())

    @property
    def online_bookings(self):
        return self._valid_online_booking_query().distinct().all()

    @property
    def online_attendees(self):
        """Distinct users currently entitled to this event's stream. Reflects the
        *current* booking.user_id, so a completed ticket transfer automatically moves
        the attendee from old owner to new owner with no double-counting."""
        bookings = self._valid_online_booking_query().distinct().all()
        seen, people = set(), []
        for b in bookings:
            if b.user_id not in seen:
                seen.add(b.user_id)
                people.append(b.user)
        return people

    @property
    def physical_tickets_sold(self):
        return (db.session.query(db.func.coalesce(db.func.sum(BookingItem.quantity), 0))
                .join(Booking, Booking.id == BookingItem.booking_id)
                .join(Payment, Payment.booking_id == Booking.id)
                .filter(Booking.event_id == self.id, Booking.status == "confirmed",
                        Payment.status == "success", BookingItem.is_streaming.is_(False)).scalar())

    @property
    def gross_revenue(self):
        return (db.session.query(db.func.coalesce(db.func.sum(Booking.total_amount), 0))
                .join(Payment, Payment.booking_id == Booking.id)
                .filter(Booking.event_id == self.id, Booking.status == "confirmed",
                        Payment.status == "success").scalar())

    @property
    def visible_reviews(self):
        return Review.query.filter_by(event_id=self.id, is_deleted=False).order_by(Review.created_at.desc()).all()

    @property
    def review_count(self):
        return Review.query.filter_by(event_id=self.id, is_deleted=False).count()

    @property
    def average_rating(self):
        avg = (db.session.query(db.func.avg(Review.rating))
               .filter(Review.event_id == self.id, Review.is_deleted == False).scalar())  # noqa: E712
        return round(avg, 1) if avg else None

    def is_reviewable_by(self, user):
        """A 'verified attendee' review requires a confirmed booking with a successful
        payment for this event — matches the same 'valid sale' definition used elsewhere."""
        if not user or not user.is_authenticated:
            return False
        return (Booking.query.join(Payment, Payment.booking_id == Booking.id)
                .filter(Booking.event_id == self.id, Booking.user_id == user.id,
                        Booking.status == "confirmed", Payment.status == "success").first() is not None)

    @property
    def stream_state(self):
        """One of NOT_ENABLED / UPCOMING / LIVE / ENDED. Uses the organizer-configured
        access_start/access_end window when set; otherwise falls back to treating the
        whole event date as the live window, since `time` is a free-text field and not
        reliably parseable into an exact datetime."""
        if self.event_type == "physical":
            return "NOT_ENABLED"
        now = datetime.now(timezone.utc)
        if self.access_start and self.access_end:
            start, end = self.access_start, self.access_end
            if start.tzinfo is None:
                start = start.replace(tzinfo=timezone.utc)
            if end.tzinfo is None:
                end = end.replace(tzinfo=timezone.utc)
            if now < start:
                return "UPCOMING"
            if now > end:
                return "ENDED"
            return "LIVE"
        today = datetime.now(timezone.utc).date()
        if today < self.date:
            return "UPCOMING"
        if today > self.date:
            return "ENDED"
        return "LIVE"


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
    discount_percent = db.Column(db.Float, nullable=False)  # e.g. 50 = 50% off (used when discount_type='percent')
    active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    # Phase 9 — admin coupon management. All additive/defaulted so the existing seeded
    # Shivendra50 coupon (percent, no caps/limits/expiry) behaves exactly as before.
    discount_type = db.Column(db.String(10), default="percent")  # percent | flat
    discount_amount = db.Column(db.Float)  # flat ₹ off — used when discount_type='flat'
    min_booking_amount = db.Column(db.Float)  # nullable — no minimum if unset
    max_discount_amount = db.Column(db.Float)  # nullable — no cap if unset
    usage_limit = db.Column(db.Integer)  # nullable — unlimited if unset
    times_used = db.Column(db.Integer, default=0)
    expiry_date = db.Column(db.Date)  # nullable — never expires if unset

    def is_valid_for(self, subtotal):
        """Single source of truth for 'can this coupon be applied right now', reused by
        both the customer-facing apply route and the price-computation function."""
        if not self.active:
            return False, "This coupon is no longer active."
        if self.expiry_date and self.expiry_date < date.today():
            return False, "This coupon has expired."
        if self.usage_limit is not None and self.times_used >= self.usage_limit:
            return False, "This coupon has reached its usage limit."
        if self.min_booking_amount and subtotal < self.min_booking_amount:
            return False, f"This coupon requires a minimum booking of ₹{self.min_booking_amount:.0f}."
        return True, None

    def compute_discount(self, subtotal):
        if self.discount_type == "flat" and self.discount_amount:
            discount = min(self.discount_amount, subtotal)
        else:
            discount = subtotal * (self.discount_percent / 100)
        if self.max_discount_amount:
            discount = min(discount, self.max_discount_amount)
        return round(discount, 2)


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
    def has_physical_item(self):
        return any(not i.is_streaming for i in self.items)

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


# ==========================================================================
# PHASE 3 — Event social layer (who's going, reactions, comments)
# ==========================================================================

REACTION_TYPES = ("love", "excited", "funny", "cant_wait", "interested")


class EventReaction(db.Model):
    """One reaction per user per event — reacting again with a different
    type switches it; reacting with the same type again removes it."""
    __tablename__ = "event_reactions"
    id = db.Column(db.Integer, primary_key=True)
    event_id = db.Column(db.Integer, db.ForeignKey("events.id"), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    reaction_type = db.Column(db.String(20), nullable=False)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    __table_args__ = (db.UniqueConstraint("event_id", "user_id", name="uq_reaction_per_user_event"),)

    event = db.relationship("Event", backref=db.backref("reactions", lazy=True, cascade="all, delete-orphan"))
    user = db.relationship("User")


class EventComment(db.Model):
    """Event discussion. parent_id supports one level of replies. Deletion
    is soft (is_deleted) so a reply thread never orphans mid-conversation —
    the row stays but renders as '[deleted]'."""
    __tablename__ = "event_comments"
    id = db.Column(db.Integer, primary_key=True)
    event_id = db.Column(db.Integer, db.ForeignKey("events.id"), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    parent_id = db.Column(db.Integer, db.ForeignKey("event_comments.id"))
    content = db.Column(db.String(500), nullable=False)
    is_deleted = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    event = db.relationship("Event", backref=db.backref("comments", lazy=True, cascade="all, delete-orphan"))
    user = db.relationship("User")
    replies = db.relationship("EventComment", backref=db.backref("parent", remote_side=[id]), lazy=True)

    @property
    def like_count(self):
        return CommentLike.query.filter_by(comment_id=self.id).count()

    @property
    def report_count(self):
        return CommentReport.query.filter_by(comment_id=self.id).count()

    def liked_by(self, user):
        if not user or not user.is_authenticated:
            return False
        return CommentLike.query.filter_by(comment_id=self.id, user_id=user.id).first() is not None


class CommentLike(db.Model):
    __tablename__ = "comment_likes"
    id = db.Column(db.Integer, primary_key=True)
    comment_id = db.Column(db.Integer, db.ForeignKey("event_comments.id"), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    __table_args__ = (db.UniqueConstraint("comment_id", "user_id", name="uq_like_per_user_comment"),)


class CommentReport(db.Model):
    """Report record feeding the unified /admin/reports queue (Phase 8)."""
    __tablename__ = "comment_reports"
    id = db.Column(db.Integer, primary_key=True)
    comment_id = db.Column(db.Integer, db.ForeignKey("event_comments.id"), nullable=False)
    reporter_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    reason = db.Column(db.String(200))
    status = db.Column(db.String(20), default="pending")  # pending | reviewed | resolved | rejected
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    __table_args__ = (db.UniqueConstraint("comment_id", "reporter_id", name="uq_report_per_user_comment"),)

    comment = db.relationship("EventComment")
    reporter = db.relationship("User")


class Review(db.Model):
    """One review per user per event, gated to users with a confirmed + paid booking
    for that event (checked at write time in the route, not enforced by a DB constraint,
    since 'eligible to review' can't be expressed as a simple FK/unique rule here)."""
    __tablename__ = "reviews"
    id = db.Column(db.Integer, primary_key=True)
    event_id = db.Column(db.Integer, db.ForeignKey("events.id"), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    rating = db.Column(db.Integer, nullable=False)  # 1-5
    content = db.Column(db.String(1000))
    is_deleted = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc),
                            onupdate=lambda: datetime.now(timezone.utc))

    __table_args__ = (db.UniqueConstraint("event_id", "user_id", name="uq_review_per_user_event"),)

    event = db.relationship("Event", backref=db.backref("reviews", lazy=True, cascade="all, delete-orphan"))
    user = db.relationship("User")

    @property
    def helpful_count(self):
        return ReviewHelpful.query.filter_by(review_id=self.id).count()

    @property
    def report_count(self):
        return ReviewReport.query.filter_by(review_id=self.id).count()

    def marked_helpful_by(self, user):
        if not user or not user.is_authenticated:
            return False
        return ReviewHelpful.query.filter_by(review_id=self.id, user_id=user.id).first() is not None


class ReviewHelpful(db.Model):
    __tablename__ = "review_helpful"
    id = db.Column(db.Integer, primary_key=True)
    review_id = db.Column(db.Integer, db.ForeignKey("reviews.id"), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    __table_args__ = (db.UniqueConstraint("review_id", "user_id", name="uq_helpful_per_user_review"),)


class ReviewReport(db.Model):
    __tablename__ = "review_reports"
    id = db.Column(db.Integer, primary_key=True)
    review_id = db.Column(db.Integer, db.ForeignKey("reviews.id"), nullable=False)
    reporter_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    reason = db.Column(db.String(200))
    status = db.Column(db.String(20), default="pending")  # pending | reviewed | resolved | rejected
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    __table_args__ = (db.UniqueConstraint("review_id", "reporter_id", name="uq_report_per_user_review"),)

    review = db.relationship("Review")
    reporter = db.relationship("User")


# ==========================================================================
# PHASE 7 — Notifications, gamification, referrals, groups/polls, Say Hi
# ==========================================================================

class Notification(db.Model):
    __tablename__ = "notifications"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    kind = db.Column(db.String(30), nullable=False)  # follow | comment_reply | booking | transfer | say_hi | badge | group | poll
    title = db.Column(db.String(160), nullable=False)
    body = db.Column(db.String(300))
    link = db.Column(db.String(300))  # relative URL to send the user to on click
    is_read = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    user = db.relationship("User", backref=db.backref("notifications", lazy=True, cascade="all, delete-orphan"))


def notify(user_id, kind, title, body=None, link=None):
    """Central helper for creating a notification — every trigger point in app.py
    calls this instead of constructing Notification rows directly, so the shape
    stays consistent. Caller is responsible for db.session.commit()."""
    db.session.add(Notification(user_id=user_id, kind=kind, title=title, body=body, link=link))


class Badge(db.Model):
    __tablename__ = "badges"
    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(40), unique=True, nullable=False)
    name = db.Column(db.String(60), nullable=False)
    emoji = db.Column(db.String(8), default="🏆")
    description = db.Column(db.String(200))


class UserBadge(db.Model):
    __tablename__ = "user_badges"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    badge_id = db.Column(db.Integer, db.ForeignKey("badges.id"), nullable=False)
    earned_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    __table_args__ = (db.UniqueConstraint("user_id", "badge_id", name="uq_badge_per_user"),)

    badge = db.relationship("Badge")
    user = db.relationship("User", backref=db.backref("user_badges", lazy=True, cascade="all, delete-orphan"))


class PointsTransaction(db.Model):
    """Append-only ledger — the source of truth for points. User.points is a cached
    sum kept in sync at award time, purely so templates don't need to aggregate this
    table on every page load."""
    __tablename__ = "points_transactions"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    points = db.Column(db.Integer, nullable=False)  # can be negative for future corrections
    reason = db.Column(db.String(120), nullable=False)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))


def award_points(user, points, reason):
    """Single choke point for granting points — updates the cached total AND writes
    the audit-trail row, so the two can never drift apart."""
    user.points = (user.points or 0) + points
    db.session.add(PointsTransaction(user_id=user.id, points=points, reason=reason))


class Referral(db.Model):
    """One row per successful referral: created only once the referred user completes
    a qualifying booking (not merely at registration), so points can't be farmed by
    creating throwaway accounts that never buy anything."""
    __tablename__ = "referrals"
    id = db.Column(db.Integer, primary_key=True)
    referrer_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    referred_user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    qualifying_booking_id = db.Column(db.Integer, db.ForeignKey("bookings.id"))
    rewarded = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    __table_args__ = (db.UniqueConstraint("referred_user_id", name="uq_referral_per_referred_user"),)

    referrer = db.relationship("User", foreign_keys=[referrer_id])
    referred_user = db.relationship("User", foreign_keys=[referred_user_id])


class GroupInvitation(db.Model):
    """Explicit invite mechanism, separate from joining via the group's URL. Any current
    member can invite; the invited user must accept before becoming a member."""
    __tablename__ = "group_invitations"
    id = db.Column(db.Integer, primary_key=True)
    group_id = db.Column(db.Integer, db.ForeignKey("event_groups.id"), nullable=False)
    invited_by_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    invited_user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    status = db.Column(db.String(20), default="pending")  # pending | accepted | rejected
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    responded_at = db.Column(db.DateTime)

    __table_args__ = (db.UniqueConstraint("group_id", "invited_user_id", name="uq_invite_per_user_group"),)

    group = db.relationship("EventGroup", backref=db.backref("invitations", lazy=True, cascade="all, delete-orphan"))
    invited_by = db.relationship("User", foreign_keys=[invited_by_id])
    invited_user = db.relationship("User", foreign_keys=[invited_user_id])


class EventGroup(db.Model):
    __tablename__ = "event_groups"
    id = db.Column(db.Integer, primary_key=True)
    event_id = db.Column(db.Integer, db.ForeignKey("events.id"), nullable=False)
    name = db.Column(db.String(80), nullable=False)
    creator_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    event = db.relationship("Event", backref=db.backref("groups", lazy=True, cascade="all, delete-orphan"))
    creator = db.relationship("User")
    members = db.relationship("EventGroupMember", backref="group", lazy=True, cascade="all, delete-orphan")

    @property
    def member_count(self):
        return len(self.members)

    def is_member(self, user):
        if not user or not user.is_authenticated:
            return False
        return any(m.user_id == user.id for m in self.members)


class EventGroupMember(db.Model):
    __tablename__ = "event_group_members"
    id = db.Column(db.Integer, primary_key=True)
    group_id = db.Column(db.Integer, db.ForeignKey("event_groups.id"), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    joined_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    __table_args__ = (db.UniqueConstraint("group_id", "user_id", name="uq_member_per_group"),)

    user = db.relationship("User")


class Poll(db.Model):
    __tablename__ = "polls"
    id = db.Column(db.Integer, primary_key=True)
    group_id = db.Column(db.Integer, db.ForeignKey("event_groups.id"), nullable=False)
    question = db.Column(db.String(200), nullable=False)
    created_by = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    group = db.relationship("EventGroup", backref=db.backref("polls", lazy=True, cascade="all, delete-orphan"))
    options = db.relationship("PollOption", backref="poll", lazy=True, cascade="all, delete-orphan")

    @property
    def total_votes(self):
        return sum(o.vote_count for o in self.options)

    def voted_by(self, user):
        if not user or not user.is_authenticated:
            return None
        v = PollVote.query.join(PollOption).filter(PollOption.poll_id == self.id, PollVote.user_id == user.id).first()
        return v.option_id if v else None


class PollOption(db.Model):
    __tablename__ = "poll_options"
    id = db.Column(db.Integer, primary_key=True)
    poll_id = db.Column(db.Integer, db.ForeignKey("polls.id"), nullable=False)
    text = db.Column(db.String(100), nullable=False)

    @property
    def vote_count(self):
        return PollVote.query.filter_by(option_id=self.id).count()


class PollVote(db.Model):
    __tablename__ = "poll_votes"
    id = db.Column(db.Integer, primary_key=True)
    option_id = db.Column(db.Integer, db.ForeignKey("poll_options.id"), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))


class SayHi(db.Model):
    """A safe, templated social introduction tied to a shared event — not open-ended
    messaging. Accept/Ignore/Block/Report per the spec; only after Accept would any
    further messaging be appropriate, and no freeform DM system is built here."""
    __tablename__ = "say_hi"
    id = db.Column(db.Integer, primary_key=True)
    event_id = db.Column(db.Integer, db.ForeignKey("events.id"), nullable=False)
    from_user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    to_user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    message = db.Column(db.String(200), nullable=False)
    status = db.Column(db.String(20), default="pending")  # pending | accepted | ignored | blocked | reported
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    __table_args__ = (db.UniqueConstraint("event_id", "from_user_id", "to_user_id", name="uq_say_hi_per_pair_event"),)

    from_user = db.relationship("User", foreign_keys=[from_user_id])
    to_user = db.relationship("User", foreign_keys=[to_user_id])
    event = db.relationship("Event")


class Block(db.Model):
    """A global block — once User A blocks User B, B can no longer send Say Hi
    requests to A (in any event), independent of the per-event SayHi row."""
    __tablename__ = "blocks"
    id = db.Column(db.Integer, primary_key=True)
    blocker_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    blocked_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    __table_args__ = (db.UniqueConstraint("blocker_id", "blocked_id", name="uq_block_pair"),)
