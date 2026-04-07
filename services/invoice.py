import logging
from flask import current_app, url_for
from models.database import get_db
from services.wallet import calc_max_coin_discount, spend_coins, COIN_VALUE_CAD
from services.sms import send_sms

log = logging.getLogger(__name__)

# BC PST + GST = 12% (5% GST + 7% PST)
TAX_RATE = 0.12


def create_invoice(job_id, coins_to_apply=0):
    """Create an invoice for a job. Optionally apply Crucible Coins as discount."""
    db = get_db()
    job = db.execute("SELECT * FROM repair_jobs WHERE id = ?", (job_id,)).fetchone()
    if not job or not job["final_cost"]:
        return None

    customer_id = job["customer_id"]
    subtotal_pre_tax = job["final_cost"]
    tax = subtotal_pre_tax * TAX_RATE
    subtotal = subtotal_pre_tax + tax

    # Cap coins at the max allowable
    max_coins = calc_max_coin_discount(subtotal, customer_id)
    coins_applied = min(max(0, int(coins_to_apply)), max_coins)
    discount = coins_applied * COIN_VALUE_CAD
    total = max(0, subtotal - discount)

    # Spend the coins from the wallet
    if coins_applied > 0:
        if not spend_coins(customer_id, coins_applied, f"Applied to invoice for Job #{job_id}", job_id=job_id):
            log.warning("Failed to spend coins for invoice on job %s", job_id)
            coins_applied = 0
            discount = 0
            total = subtotal

    cursor = db.execute(
        """INSERT INTO invoices (job_id, customer_id, subtotal, coins_applied, discount_amount, total)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (job_id, customer_id, subtotal, coins_applied, discount, total),
    )
    db.commit()
    return cursor.lastrowid


def mark_paid(invoice_id):
    db = get_db()
    db.execute(
        "UPDATE invoices SET status = 'paid', paid_at = CURRENT_TIMESTAMP WHERE id = ?",
        (invoice_id,),
    )
    db.commit()


def send_invoice_sms(invoice_id, base_url=None):
    """Text the customer their invoice details + payment link."""
    db = get_db()
    invoice = db.execute(
        """
        SELECT i.*, c.name AS customer_name, c.phone AS customer_phone, r.device_type, r.device_brand
        FROM invoices i
        JOIN customers c ON i.customer_id = c.id
        JOIN repair_jobs r ON i.job_id = r.id
        WHERE i.id = ?
        """,
        (invoice_id,),
    ).fetchone()
    if not invoice:
        return False

    business = current_app.config.get("BUSINESS_NAME", "The Crucible")
    device = f"{invoice['device_brand'] or ''} {invoice['device_type']}".strip()

    lines = [
        f"Hi {invoice['customer_name']}, your invoice from {business} is ready.",
        f"{device} (Job #{invoice['job_id']})",
        f"Subtotal: ${invoice['subtotal']:.2f}",
    ]
    if invoice["coins_applied"] > 0:
        lines.append(f"Coin discount: -${invoice['discount_amount']:.2f} ({int(invoice['coins_applied'])} coins)")
    lines.append(f"Total due: ${invoice['total']:.2f}")
    lines.append(f"Invoice #{invoice['id']}")

    message = "\n".join(lines)
    sent = send_sms(invoice["customer_phone"], message)

    db.execute(
        "INSERT INTO sms_log (job_id, phone, message, status) VALUES (?, ?, ?, ?)",
        (invoice["job_id"], invoice["customer_phone"], message, "sent" if sent else "failed"),
    )
    db.commit()
    return sent
