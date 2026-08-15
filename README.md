# 🎟️ EVENTIFY — Event Management & Online Ticket Booking Platform

Discover Events. Create Memories.

A full-stack event discovery and ticket booking platform with a premium
dark-glassmorphism UI, a complete booking flow with simulated payments and
QR-coded digital tickets, and a full admin panel with analytics.

## Tech Stack

- **Backend:** Python, Flask, Flask-Login
- **Database:** SQLite via SQLAlchemy (MySQL-compatible architecture)
- **Frontend:** HTML5, CSS3 (custom glassmorphism design system), Bootstrap 5, vanilla JS
- **Libraries:** Chart.js (analytics), AOS (scroll animations), Font Awesome, qrcode + Pillow (ticket QR codes)

## Setup

```bash
# 1. Create a virtual environment (recommended)
python3 -m venv venv
source venv/bin/activate      # Windows: venv\Scripts\activate

# 2. Install dependencies
pip install -r requirements.txt

# 3. Seed the database with sample events + demo accounts
python seed.py

# 4. Run the app
python app.py
```

The app runs at **http://127.0.0.1:5000**

## Demo Accounts

| Role  | Email                | Password  |
|-------|-----------------------|-----------|
| Admin | admin@eventify.com    | admin123  |
| User  | demo@eventify.com     | demo1234  |

Admin panel: **http://127.0.0.1:5000/admin/login**

## Project Structure

```
eventify/
├── app.py                 # Flask routes (public, auth, booking, admin)
├── models.py               # SQLAlchemy models (Users, Events, Bookings, etc.)
├── seed.py                  # Sample data + demo account seeder
├── requirements.txt
├── static/
│   ├── css/style.css        # Design system (dark glassmorphism)
│   └── js/
│       ├── main.js          # Navbar, counters, wishlist, particles
│       └── booking.js       # Ticket qty, payment simulation, confetti
└── templates/
    ├── base.html, index.html, events.html, event_detail.html
    ├── login.html, register.html
    ├── dashboard.html, my_bookings.html, wishlist.html, profile.html
    ├── booking_tickets.html, booking_details.html, payment.html,
    │   confirmation.html, ticket.html, _ticket_card.html
    ├── errors/404.html, errors/403.html
    └── admin/
        ├── admin_base.html, login.html, dashboard.html
        ├── events.html, event_form.html, users.html
        └── bookings.html, analytics.html
```

## Features

- Event discovery with search, category/location filters, and sorting
- Ticket selection with live price calculation (price × qty + 5% convenience fee)
- Simulated payment (UPI / Card / Net Banking) with processing + success + confetti animation
- Digital ticket with QR code, downloadable via browser print
- User dashboard: bookings, wishlist, profile management
- Full admin panel: event CRUD, user management, booking oversight, Chart.js analytics
- Role-based access control, hashed passwords, session-based auth
- Fully responsive, animated (AOS scroll reveals, animated counters, glassmorphism cards)

## Notes

- Payments are **simulated only** — no real transactions occur.
- To reset all data, delete `eventify.db` and re-run `python seed.py`.
- For production, replace the Flask dev server with a WSGI server (e.g. gunicorn) and set a proper `SECRET_KEY`.
