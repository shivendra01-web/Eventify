// ---------- Navbar scroll transition ----------
const navbar = document.querySelector(".navbar-eventify");
if (navbar) {
  window.addEventListener("scroll", () => {
    navbar.classList.toggle("scrolled", window.scrollY > 40);
  });
}

// ---------- Animated background particles ----------
(function initParticles() {
  const scene = document.querySelector(".bg-scene");
  if (!scene) return;
  const count = window.innerWidth < 768 ? 14 : 28;
  for (let i = 0; i < count; i++) {
    const p = document.createElement("div");
    p.className = "particle";
    p.style.left = Math.random() * 100 + "%";
    p.style.animationDelay = Math.random() * 14 + "s";
    p.style.animationDuration = 10 + Math.random() * 10 + "s";
    scene.appendChild(p);
  }
})();

// ---------- Animated stat counters ----------
function animateCounter(el) {
  const target = parseInt(el.dataset.count, 10) || 0;
  const duration = 1600;
  const start = performance.now();
  function tick(now) {
    const progress = Math.min((now - start) / duration, 1);
    const eased = 1 - Math.pow(1 - progress, 3);
    el.textContent = Math.floor(eased * target).toLocaleString("en-IN");
    if (progress < 1) requestAnimationFrame(tick);
    else el.textContent = target.toLocaleString("en-IN");
  }
  requestAnimationFrame(tick);
}

const counterObserver = new IntersectionObserver((entries) => {
  entries.forEach((entry) => {
    if (entry.isIntersecting && !entry.target.dataset.animated) {
      entry.target.dataset.animated = "true";
      animateCounter(entry.target);
    }
  });
}, { threshold: 0.4 });

document.querySelectorAll("[data-count]").forEach((el) => counterObserver.observe(el));

// ---------- Wishlist toggle ----------
document.querySelectorAll(".wishlist-btn").forEach((btn) => {
  btn.addEventListener("click", async (e) => {
    e.preventDefault();
    e.stopPropagation();
    const eventId = btn.dataset.eventId;
    if (!eventId) return;
    if (btn.dataset.authRequired === "true") {
      window.location.href = "/login";
      return;
    }
    try {
      const res = await fetch(`/wishlist/toggle/${eventId}`, { method: "POST" });
      const data = await res.json();
      btn.classList.toggle("active", data.status === "added");
      btn.classList.add("pulse");
      setTimeout(() => btn.classList.remove("pulse"), 500);
      const icon = btn.querySelector("i");
      if (icon) icon.className = data.status === "added" ? "fa-solid fa-heart" : "fa-regular fa-heart";
    } catch (err) {
      console.error(err);
    }
  });
});

// ---------- Mobile menu ----------
const mobileToggle = document.querySelector(".navbar-toggler");
const mobileMenu = document.querySelector("#navbarNav");
if (mobileToggle && mobileMenu) {
  mobileToggle.addEventListener("click", () => {
    mobileMenu.classList.toggle("show-anim");
  });
}

// ---------- Admin sidebar toggle (mobile) ----------
const sidebarToggle = document.querySelector("#sidebarToggle");
const adminSidebar = document.querySelector(".admin-sidebar");
if (sidebarToggle && adminSidebar) {
  sidebarToggle.addEventListener("click", () => adminSidebar.classList.toggle("open"));
}

// ---------- Auto-dismiss alerts ----------
document.querySelectorAll(".alert-glass").forEach((alert) => {
  setTimeout(() => {
    alert.style.transition = "opacity .5s ease";
    alert.style.opacity = "0";
    setTimeout(() => alert.remove(), 500);
  }, 5000);
});

// ---------- Password visibility toggle ----------
document.querySelectorAll(".toggle-password").forEach((toggle) => {
  toggle.addEventListener("click", () => {
    const input = document.querySelector(toggle.dataset.target);
    if (!input) return;
    const isPassword = input.type === "password";
    input.type = isPassword ? "text" : "password";
    toggle.querySelector("i").className = isPassword ? "fa-regular fa-eye-slash" : "fa-regular fa-eye";
  });
});

// ---------- FAQ accordion caret rotation handled by Bootstrap; add rotation class ----------
document.querySelectorAll(".faq-toggle").forEach((btn) => {
  btn.addEventListener("click", () => {
    setTimeout(() => btn.classList.toggle("rotated"), 10);
  });
});

// ---------- Confetti burst (lightweight canvas confetti, shared site-wide) ----------
function launchConfetti() {
  const canvas = document.createElement("canvas");
  canvas.style.position = "fixed";
  canvas.style.inset = "0";
  canvas.style.zIndex = "3000";
  canvas.style.pointerEvents = "none";
  canvas.width = window.innerWidth;
  canvas.height = window.innerHeight;
  document.body.appendChild(canvas);
  const ctx = canvas.getContext("2d");
  const colors = ["#7c3aed", "#2563eb", "#22d3ee", "#ec4899", "#f59e0b"];
  const pieces = Array.from({ length: 140 }, () => ({
    x: Math.random() * canvas.width,
    y: -20 - Math.random() * canvas.height * 0.5,
    r: 4 + Math.random() * 5,
    color: colors[Math.floor(Math.random() * colors.length)],
    speedY: 2 + Math.random() * 3,
    speedX: -1.5 + Math.random() * 3,
    rotation: Math.random() * 360,
    rotSpeed: -6 + Math.random() * 12,
  }));

  let frame = 0;
  function draw() {
    frame++;
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    pieces.forEach((p) => {
      p.y += p.speedY;
      p.x += p.speedX;
      p.rotation += p.rotSpeed;
      ctx.save();
      ctx.translate(p.x, p.y);
      ctx.rotate((p.rotation * Math.PI) / 180);
      ctx.fillStyle = p.color;
      ctx.fillRect(-p.r / 2, -p.r / 2, p.r, p.r * 0.6);
      ctx.restore();
    });
    if (frame < 130) requestAnimationFrame(draw);
    else canvas.remove();
  }
  draw();
}

// ---------- Small toast helper (used by login celebration + coupon success) ----------
function showEventifyToast(message, opts) {
  opts = opts || {};
  const toast = document.createElement("div");
  toast.className = "eventify-toast" + (opts.className ? " " + opts.className : "");
  toast.textContent = message;
  document.body.appendChild(toast);
  requestAnimationFrame(() => toast.classList.add("show"));
  setTimeout(() => {
    toast.classList.remove("show");
    setTimeout(() => toast.remove(), 400);
  }, opts.duration || 2600);
}

// ---------- Login/signup celebration (triggers once, right after a successful login) ----------
const celebrateEl = document.querySelector("#celebrateLogin");
if (celebrateEl) {
  window.addEventListener("load", () => {
    setTimeout(() => {
      launchConfetti();
      showEventifyToast(celebrateEl.dataset.message || "Welcome to Eventify! 🎉", { duration: 3200 });
    }, 250);
  });
}
