// ---------- Ticket quantity + live price summary ----------
(function initTicketSelector() {
  const rows = document.querySelectorAll(".ticket-row");
  if (!rows.length) return;
  const subtotalEl = document.querySelector("#subtotalAmount");
  const feeEl = document.querySelector("#feeAmount");
  const totalEl = document.querySelector("#totalAmount");
  const availableSeats = parseInt(document.querySelector("#availableSeats")?.dataset.seats || "0", 10);
  const warningEl = document.querySelector("#seatWarning");

  function recalc() {
    let subtotal = 0;
    let totalQty = 0;
    rows.forEach((row) => {
      const price = parseFloat(row.dataset.price);
      const qtyInput = row.querySelector(".qty-input");
      const qty = parseInt(qtyInput.value, 10) || 0;
      const lineTotal = price * qty;
      row.querySelector(".line-total").textContent = "₹" + lineTotal.toLocaleString("en-IN");
      subtotal += lineTotal;
      totalQty += qty;
    });
    const fee = Math.round(subtotal * 0.05);
    const total = subtotal + fee;
    if (subtotalEl) subtotalEl.textContent = "₹" + subtotal.toLocaleString("en-IN");
    if (feeEl) feeEl.textContent = "₹" + fee.toLocaleString("en-IN");
    if (totalEl) totalEl.textContent = "₹" + total.toLocaleString("en-IN");

    const submitBtn = document.querySelector("#proceedBtn");
    if (warningEl) {
      if (totalQty > availableSeats) {
        warningEl.classList.remove("d-none");
        if (submitBtn) submitBtn.disabled = true;
      } else {
        warningEl.classList.add("d-none");
        if (submitBtn) submitBtn.disabled = totalQty === 0;
      }
    }
  }

  rows.forEach((row) => {
    const qtyInput = row.querySelector(".qty-input");
    row.querySelector(".qty-plus")?.addEventListener("click", () => {
      qtyInput.value = (parseInt(qtyInput.value, 10) || 0) + 1;
      recalc();
    });
    row.querySelector(".qty-minus")?.addEventListener("click", () => {
      qtyInput.value = Math.max((parseInt(qtyInput.value, 10) || 0) - 1, 0);
      recalc();
    });
    qtyInput.addEventListener("input", recalc);
  });

  recalc();
})();

// ---------- Meal option selection (visual only; radio input already in the DOM) ----------
document.querySelectorAll(".meal-option").forEach((opt) => {
  opt.addEventListener("click", () => {
    document.querySelectorAll(".meal-option").forEach((o) => o.classList.remove("selected"));
    opt.classList.add("selected");
    const radio = opt.querySelector('input[type="radio"]');
    if (radio) radio.checked = true;
  });
});

// ---------- Coupon apply / remove (server always recalculates — this just renders the response) ----------
(function initCoupon() {
  const applyBtn = document.querySelector("#applyCouponBtn");
  const removeBtn = document.querySelector("#removeCouponBtn");
  if (!applyBtn && !removeBtn) return;

  const eventId = applyBtn ? applyBtn.dataset.eventId : removeBtn.dataset.eventId;
  const codeInput = document.querySelector("#couponCodeInput");
  const messageEl = document.querySelector("#couponMessage");
  const inputRow = document.querySelector("#couponInputRow");
  const appliedRow = document.querySelector("#couponAppliedRow");
  const codeLabel = document.querySelector("#couponCodeLabel");

  function renderPricing(pricing) {
    document.querySelector("#pxSubtotal").textContent = "₹" + Math.round(pricing.subtotal).toLocaleString("en-IN");
    document.querySelector("#pxFee").textContent = "₹" + Math.round(pricing.fee).toLocaleString("en-IN");
    document.querySelector("#pxTotal").textContent = "₹" + Math.round(pricing.total).toLocaleString("en-IN");
    const discountRow = document.querySelector("#pxDiscountRow");
    if (pricing.discount > 0) {
      document.querySelector("#pxDiscount").textContent = "-₹" + Math.round(pricing.discount).toLocaleString("en-IN");
      document.querySelector("#pxDiscountCode").textContent = pricing.applied_coupon ? "(" + pricing.applied_coupon + ")" : "";
      discountRow.classList.remove("d-none");
    } else {
      discountRow.classList.add("d-none");
    }
  }

  function showMessage(text, isError) {
    if (!messageEl) return;
    messageEl.textContent = text;
    messageEl.classList.remove("d-none");
    messageEl.style.color = isError ? "var(--danger)" : "var(--success)";
  }

  if (applyBtn) {
    applyBtn.addEventListener("click", () => {
      const code = (codeInput.value || "").trim();
      if (!code) { showMessage("Please enter a coupon code.", true); return; }
      applyBtn.disabled = true;
      fetch(`/book/${eventId}/coupon/apply`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ code }),
      })
        .then((r) => r.json())
        .then((data) => {
          applyBtn.disabled = false;
          if (data.ok) {
            renderPricing(data.pricing);
            if (codeLabel) codeLabel.textContent = data.pricing.applied_coupon;
            inputRow.classList.add("d-none");
            appliedRow.classList.remove("d-none");
            showMessage(data.message, false);
            launchConfetti();
            showEventifyToast("50% OFF Applied! 🎉", { duration: 2200 });
          } else {
            showMessage(data.error || "Could not apply coupon.", true);
          }
        })
        .catch(() => { applyBtn.disabled = false; showMessage("Network error. Please try again.", true); });
    });
  }

  if (removeBtn) {
    removeBtn.addEventListener("click", () => {
      fetch(`/book/${eventId}/coupon/remove`, { method: "POST" })
        .then((r) => r.json())
        .then((data) => {
          if (data.ok) {
            renderPricing(data.pricing);
            appliedRow.classList.add("d-none");
            inputRow.classList.remove("d-none");
            if (codeInput) codeInput.value = "";
            if (messageEl) messageEl.classList.add("d-none");
          }
        });
    });
  }
})();

// ---------- Razorpay checkout ----------
const rzpBtn = document.querySelector("#rzpPayBtn");
if (rzpBtn && window.EVENTIFY_RAZORPAY) {
  const cfg = window.EVENTIFY_RAZORPAY;
  const overlay = document.querySelector("#processingOverlay");
  const errorBox = document.querySelector("#paymentError");

  function showError(msg) {
    if (!errorBox) return;
    errorBox.textContent = msg;
    errorBox.classList.remove("d-none");
  }

  rzpBtn.addEventListener("click", () => {
    if (errorBox) { errorBox.classList.add("d-none"); errorBox.textContent = ""; }

    const rzp = new Razorpay({
      key: cfg.keyId,
      amount: cfg.amount,
      currency: "INR",
      name: "Eventify",
      description: cfg.eventTitle,
      order_id: cfg.orderId,
      prefill: {
        name: cfg.customerName,
        email: cfg.customerEmail,
        contact: cfg.customerPhone,
      },
      theme: { color: "#7c3aed" },
      handler: function (response) {
        // Checkout closed with a payment response — this is NOT proof of a
        // successful payment yet. We still have to verify the signature
        // server-side before the booking is created.
        if (overlay) {
          overlay.classList.remove("d-none");
          document.querySelector("#processingStep")?.classList.remove("d-none");
          document.querySelector("#successStep")?.classList.add("d-none");
        }
        fetch(cfg.verifyUrl, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            razorpay_order_id: response.razorpay_order_id,
            razorpay_payment_id: response.razorpay_payment_id,
            razorpay_signature: response.razorpay_signature,
          }),
        })
          .then((r) => r.json())
          .then((data) => {
            if (data.ok) {
              document.querySelector("#processingStep")?.classList.add("d-none");
              document.querySelector("#successStep")?.classList.remove("d-none");
              launchConfetti();
              setTimeout(() => { window.location.href = data.redirect; }, 1400);
            } else {
              overlay?.classList.add("d-none");
              showError(data.error || "Payment verification failed. Please try again.");
              if (data.redirect) setTimeout(() => { window.location.href = data.redirect; }, 2500);
            }
          })
          .catch(() => {
            overlay?.classList.add("d-none");
            showError("Network error while verifying payment. If money was deducted, it will be auto-refunded — contact support with your payment ID if it doesn't reflect within a few days.");
          });
      },
      modal: {
        ondismiss: function () {
          // User closed the Razorpay popup without paying — no booking is created.
        },
      },
    });

    rzp.on("payment.failed", function (response) {
      showError("Payment failed: " + (response.error && response.error.description ? response.error.description : "please try again."));
    });

    rzp.open();
  });
}

// Confetti burst is defined globally in main.js (loaded before this file),
// so both the login celebration and the payment-success celebration can reuse it.

// Trigger confetti automatically on confirmation page load
if (document.querySelector("#confettiOnLoad")) {
  window.addEventListener("load", () => setTimeout(launchConfetti, 300));
}
