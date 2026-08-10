import logging
from models.database import get_db

log = logging.getLogger(__name__)

# ── Crucible Coin Constants ──────────────────────────────────────────
# 1 Crucible Coin = $1 CAD store credit
COIN_VALUE_CAD = 1.00

# Earn rates
COINS_PER_COMPLETED_JOB = 5       # flat reward for completing a repair
COINS_PER_DOLLAR_SPENT = 0.10     # earn 1 coin per $10 spent
REFERRAL_BONUS_COINS = 10         # bonus for referring a new customer
MAX_COIN_DISCOUNT_PERCENT = 0.25  # max 25% of a bill can be paid in coins


def get_wallet(customer_id):
    """Get or create a wallet for a customer. Returns the wallet row."""
    db = get_db()
    wallet = db.execute(
        "SELECT * FROM wallets WHERE customer_id = ?", (customer_id,)
    ).fetchone()

    if not wallet:
        db.execute(
            "INSERT INTO wallets (customer_id, balance) VALUES (?, 0)",
            (customer_id,),
        )
        db.commit()
        wallet = db.execute(
            "SELECT * FROM wallets WHERE customer_id = ?", (customer_id,)
        ).fetchone()

    return wallet


def get_balance(customer_id):
    """Get the Crucible Coin balance for a customer."""
    wallet = get_wallet(customer_id)
    return wallet["balance"]


def add_coins(customer_id, amount, reason, job_id=None):
    """Credit coins to a customer's wallet."""
    if amount <= 0:
        return False

    db = get_db()
    wallet = get_wallet(customer_id)
    new_balance = wallet["balance"] + amount

    db.execute(
        "UPDATE wallets SET balance = ?, updated_at = CURRENT_TIMESTAMP WHERE customer_id = ?",
        (new_balance, customer_id),
    )
    db.execute(
        "INSERT INTO wallet_transactions (wallet_id, type, amount, balance_after, reason, job_id) VALUES (?, 'credit', ?, ?, ?, ?)",
        (wallet["id"], amount, new_balance, reason, job_id),
    )
    db.commit()
    log.info("Credited %s coins to customer %s — %s", amount, customer_id, reason)
    return True


def spend_coins(customer_id, amount, reason, job_id=None):
    """Debit coins from a customer's wallet. Returns False if insufficient balance."""
    if amount <= 0:
        return False

    db = get_db()
    wallet = get_wallet(customer_id)

    if wallet["balance"] < amount:
        log.warning("Insufficient balance for customer %s: has %s, needs %s", customer_id, wallet["balance"], amount)
        return False

    new_balance = wallet["balance"] - amount

    db.execute(
        "UPDATE wallets SET balance = ?, updated_at = CURRENT_TIMESTAMP WHERE customer_id = ?",
        (new_balance, customer_id),
    )
    db.execute(
        "INSERT INTO wallet_transactions (wallet_id, type, amount, balance_after, reason, job_id) VALUES (?, 'debit', ?, ?, ?, ?)",
        (wallet["id"], amount, new_balance, reason, job_id),
    )
    db.commit()
    log.info("Debited %s coins from customer %s — %s", amount, customer_id, reason)
    return True


def reward_completed_job(job_id):
    """Award coins when a repair job is completed. Idempotent — skips if already rewarded."""
    db = get_db()
    already = db.execute(
        "SELECT id FROM wallet_transactions WHERE job_id = ? AND reason LIKE 'Repair completed%'",
        (job_id,),
    ).fetchone()
    if already:
        return 0

    job = db.execute(
        "SELECT * FROM repair_jobs WHERE id = ?", (job_id,)
    ).fetchone()
    if not job:
        return 0

    total_coins = COINS_PER_COMPLETED_JOB
    if job["final_cost"] and job["final_cost"] > 0:
        total_coins += int(job["final_cost"] * COINS_PER_DOLLAR_SPENT)

    add_coins(
        job["customer_id"],
        total_coins,
        f"Repair completed — Job #{job_id}",
        job_id=job_id,
    )
    return total_coins


def calc_max_coin_discount(total_cost, customer_id):
    """Calculate max coins a customer can apply to a bill."""
    balance = get_balance(customer_id)
    max_from_bill = int(total_cost * MAX_COIN_DISCOUNT_PERCENT / COIN_VALUE_CAD)
    return min(balance, max_from_bill)


def get_transaction_history(customer_id):
    """Get full transaction history for a customer's wallet."""
    db = get_db()
    wallet = get_wallet(customer_id)
    return db.execute(
        "SELECT * FROM wallet_transactions WHERE wallet_id = ? ORDER BY created_at DESC",
        (wallet["id"],),
    ).fetchall()
