from flask import Flask, render_template, request, redirect, url_for, flash, session
from config import Config
from models.database import get_db, close_db, init_db, VALID_STATUSES
from services.sms import notify_customer
from services.wallet import get_wallet, get_balance, add_coins, spend_coins, reward_completed_job, get_transaction_history, calc_max_coin_discount, COIN_VALUE_CAD
from services.invoice import create_invoice, mark_paid, send_invoice_sms, preview_invoice, TAX_RATE
from services.auth import login_required, get_admin, create_admin, verify_password, admin_exists
from datetime import datetime


def create_app():
    app = Flask(__name__)
    app.config.from_object(Config)
    app.teardown_appcontext(close_db)

    with app.app_context():
        init_db()

    # ── Auth ─────────────────────────────────────────────────────────

    @app.route("/login", methods=["GET", "POST"])
    def login():
        if not admin_exists():
            return redirect(url_for("setup"))
        if request.method == "POST":
            username = request.form.get("username", "").strip()
            password = request.form.get("password", "")
            admin = get_admin(username)
            if admin and verify_password(password, admin["password_hash"]):
                session["admin_id"] = admin["id"]
                session["admin_username"] = admin["username"]
                flash(f"Welcome back, {username}.", "success")
                return redirect(request.args.get("next") or url_for("dashboard"))
            flash("Invalid username or password.", "error")
        return render_template("login.html")

    @app.route("/logout")
    def logout():
        session.clear()
        flash("Logged out.", "success")
        return redirect(url_for("login"))

    @app.route("/setup", methods=["GET", "POST"])
    def setup():
        if admin_exists():
            return redirect(url_for("login"))
        if request.method == "POST":
            username = request.form.get("username", "").strip()
            password = request.form.get("password", "")
            if not username or not password:
                flash("Username and password are required.", "error")
                return render_template("setup.html")
            if len(password) < 6:
                flash("Password must be at least 6 characters.", "error")
                return render_template("setup.html")
            create_admin(username, password)
            session["admin_id"] = get_admin(username)["id"]
            session["admin_username"] = username
            flash(f"Admin account '{username}' created. You're logged in.", "success")
            return redirect(url_for("dashboard"))
        return render_template("setup.html")

    # ── Admin Dashboard ──────────────────────────────────────────────

    @app.route("/")
    @login_required
    def dashboard():
        db = get_db()
        search = request.args.get("q", "").strip()
        status_filter = request.args.get("status", "").strip()

        query = """
            SELECT r.*, c.name AS customer_name, c.phone AS customer_phone
            FROM repair_jobs r
            JOIN customers c ON r.customer_id = c.id
        """
        conditions = []
        params = []

        if search:
            conditions.append(
                "(c.name LIKE ? OR c.phone LIKE ? OR r.device_brand LIKE ? OR r.device_model LIKE ? OR r.issue_description LIKE ? OR CAST(r.id AS TEXT) = ?)"
            )
            like = f"%{search}%"
            params.extend([like, like, like, like, like, search])

        if status_filter and status_filter in VALID_STATUSES:
            conditions.append("r.status = ?")
            params.append(status_filter)

        if conditions:
            query += " WHERE " + " AND ".join(conditions)

        query += " ORDER BY r.updated_at DESC"
        jobs = db.execute(query, params).fetchall()
        return render_template("dashboard.html", jobs=jobs, statuses=VALID_STATUSES, search=search, status_filter=status_filter)

    @app.route("/job/<int:job_id>")
    @login_required
    def job_detail(job_id):
        db = get_db()
        job = db.execute(
            """
            SELECT r.*, c.name AS customer_name, c.phone AS customer_phone, c.email AS customer_email
            FROM repair_jobs r
            JOIN customers c ON r.customer_id = c.id
            WHERE r.id = ?
            """,
            (job_id,),
        ).fetchone()
        if not job:
            flash("Job not found.", "error")
            return redirect(url_for("dashboard"))

        history = db.execute(
            "SELECT * FROM status_history WHERE job_id = ? ORDER BY changed_at DESC",
            (job_id,),
        ).fetchall()
        sms_history = db.execute(
            "SELECT * FROM sms_log WHERE job_id = ? ORDER BY sent_at DESC",
            (job_id,),
        ).fetchall()
        wallet_balance = get_balance(job["customer_id"])
        return render_template(
            "job_detail.html", job=job, history=history, sms_history=sms_history,
            statuses=VALID_STATUSES, wallet_balance=wallet_balance,
        )

    @app.route("/job/<int:job_id>/update-status", methods=["POST"])
    @login_required
    def update_status(job_id):
        new_status = request.form.get("status")
        note = request.form.get("note", "")

        if new_status not in VALID_STATUSES:
            flash("Invalid status.", "error")
            return redirect(url_for("job_detail", job_id=job_id))

        db = get_db()
        job = db.execute(
            """
            SELECT r.*, c.name AS customer_name, c.phone AS customer_phone
            FROM repair_jobs r
            JOIN customers c ON r.customer_id = c.id
            WHERE r.id = ?
            """,
            (job_id,),
        ).fetchone()
        if not job:
            flash("Job not found.", "error")
            return redirect(url_for("dashboard"))

        old_status = job["status"]
        db.execute(
            "UPDATE repair_jobs SET status = ?, updated_at = ? WHERE id = ?",
            (new_status, datetime.now(), job_id),
        )
        db.execute(
            "INSERT INTO status_history (job_id, old_status, new_status, note) VALUES (?, ?, ?, ?)",
            (job_id, old_status, new_status, note),
        )
        db.commit()

        # ── SMS Notification ─────────────────────────────────────
        result = notify_customer(
            job_id=job_id,
            new_status=new_status,
            customer_name=job["customer_name"],
            customer_phone=job["customer_phone"],
            device_type=job["device_type"],
            device_brand=job["device_brand"],
            device_model=job["device_model"],
        )
        if result:
            sms_status = "sent" if result["sent"] else "failed"
            db.execute(
                "INSERT INTO sms_log (job_id, phone, message, status) VALUES (?, ?, ?, ?)",
                (job_id, result["phone"], result["message"], sms_status),
            )
            db.commit()
            if result["sent"]:
                flash(f"Status updated & SMS sent to {result['phone']}.", "success")
            else:
                flash(f"Status updated to {new_status.replace('_', ' ').title()}. SMS notification pending (Twilio not configured).", "success")
        else:
            flash(f"Status updated to {new_status.replace('_', ' ').title()}.", "success")

        # ── Crucible Coin Reward ─────────────────────────────────
        if new_status == "completed":
            coins = reward_completed_job(job_id)
            if coins > 0:
                flash(f"Customer earned {coins} Crucible Coins!", "success")

        return redirect(url_for("job_detail", job_id=job_id))

    # ── SMS Log ───────────────────────────────────────────────────────

    @app.route("/sms-log")
    @login_required
    def sms_log():
        db = get_db()
        logs = db.execute(
            """
            SELECT s.*, r.device_type, c.name AS customer_name
            FROM sms_log s
            JOIN repair_jobs r ON s.job_id = r.id
            JOIN customers c ON r.customer_id = c.id
            ORDER BY s.sent_at DESC
            """
        ).fetchall()
        return render_template("sms_log.html", logs=logs)

    # ── Wallet / Crucible Coin ───────────────────────────────────────

    @app.route("/wallets")
    @login_required
    def wallets():
        db = get_db()
        wallet_list = db.execute(
            """
            SELECT w.*, c.name AS customer_name, c.phone AS customer_phone
            FROM wallets w
            JOIN customers c ON w.customer_id = c.id
            ORDER BY w.balance DESC
            """
        ).fetchall()
        return render_template("wallets.html", wallets=wallet_list, coin_value=COIN_VALUE_CAD)

    @app.route("/wallet/<int:customer_id>")
    @login_required
    def wallet_detail(customer_id):
        db = get_db()
        customer = db.execute("SELECT * FROM customers WHERE id = ?", (customer_id,)).fetchone()
        if not customer:
            flash("Customer not found.", "error")
            return redirect(url_for("wallets"))

        wallet = get_wallet(customer_id)
        transactions = get_transaction_history(customer_id)
        return render_template(
            "wallet_detail.html",
            customer=customer,
            wallet=wallet,
            transactions=transactions,
            coin_value=COIN_VALUE_CAD,
        )

    @app.route("/wallet/<int:customer_id>/adjust", methods=["POST"])
    @login_required
    def wallet_adjust(customer_id):
        action = request.form.get("action")
        amount = request.form.get("amount", "0")
        reason = request.form.get("reason", "").strip()

        try:
            amount = float(amount)
        except ValueError:
            flash("Invalid amount.", "error")
            return redirect(url_for("wallet_detail", customer_id=customer_id))

        if amount <= 0:
            flash("Amount must be positive.", "error")
            return redirect(url_for("wallet_detail", customer_id=customer_id))

        if not reason:
            reason = "Manual adjustment by admin"

        if action == "credit":
            add_coins(customer_id, amount, reason)
            flash(f"Credited {amount:.0f} Crucible Coins.", "success")
        elif action == "debit":
            if not spend_coins(customer_id, amount, reason):
                flash("Insufficient balance.", "error")
                return redirect(url_for("wallet_detail", customer_id=customer_id))
            flash(f"Debited {amount:.0f} Crucible Coins.", "success")
        else:
            flash("Invalid action.", "error")

        return redirect(url_for("wallet_detail", customer_id=customer_id))

    # ── Invoices ─────────────────────────────────────────────────────

    @app.route("/invoices")
    @login_required
    def invoices():
        db = get_db()
        invoice_list = db.execute(
            """
            SELECT i.*, c.name AS customer_name, r.device_type
            FROM invoices i
            JOIN customers c ON i.customer_id = c.id
            JOIN repair_jobs r ON i.job_id = r.id
            ORDER BY i.created_at DESC
            """
        ).fetchall()
        return render_template("invoices.html", invoices=invoice_list)

    @app.route("/job/<int:job_id>/invoice/new", methods=["GET", "POST"])
    @login_required
    def new_invoice(job_id):
        if request.method == "POST":
            try:
                coins = int(request.form.get("coins_to_apply", 0))
            except ValueError:
                coins = 0

            invoice_id = create_invoice(job_id, coins_to_apply=coins)
            if invoice_id:
                flash(f"Invoice #{invoice_id} created.", "success")
                return redirect(url_for("invoice_detail", invoice_id=invoice_id))
            flash("Set a final cost on the job before creating an invoice.", "error")
            return redirect(url_for("job_detail", job_id=job_id))

        preview = preview_invoice(job_id)
        if not preview:
            flash("Job not found or final cost not set.", "error")
            return redirect(url_for("job_detail", job_id=job_id))

        return render_template(
            "invoice_new.html",
            job=preview["job"],
            subtotal_pre_tax=preview["pre_tax"],
            tax=preview["tax"],
            subtotal=preview["subtotal"],
            tax_rate=TAX_RATE,
            max_coins=preview["max_coins"],
            balance=preview["balance"],
            coin_value=COIN_VALUE_CAD,
        )

    @app.route("/invoice/<int:invoice_id>")
    @login_required
    def invoice_detail(invoice_id):
        db = get_db()
        invoice = db.execute(
            """
            SELECT i.*, c.name AS customer_name, c.phone AS customer_phone, c.email AS customer_email,
                   r.device_type, r.device_brand, r.device_model, r.issue_description
            FROM invoices i
            JOIN customers c ON i.customer_id = c.id
            JOIN repair_jobs r ON i.job_id = r.id
            WHERE i.id = ?
            """,
            (invoice_id,),
        ).fetchone()
        if not invoice:
            flash("Invoice not found.", "error")
            return redirect(url_for("invoices"))
        return render_template("invoice_detail.html", invoice=invoice, tax_rate=TAX_RATE)

    @app.route("/invoice/<int:invoice_id>/mark-paid", methods=["POST"])
    @login_required
    def invoice_mark_paid(invoice_id):
        mark_paid(invoice_id)
        flash("Invoice marked as paid.", "success")
        return redirect(url_for("invoice_detail", invoice_id=invoice_id))

    @app.route("/invoice/<int:invoice_id>/send-sms", methods=["POST"])
    @login_required
    def invoice_send_sms(invoice_id):
        if send_invoice_sms(invoice_id):
            flash("Invoice texted to customer.", "success")
        else:
            flash("SMS failed (check Twilio config).", "error")
        return redirect(url_for("invoice_detail", invoice_id=invoice_id))

    # ── Customer Booking ─────────────────────────────────────────────

    @app.route("/book", methods=["GET", "POST"])
    def book_repair():
        if request.method == "POST":
            name = request.form.get("name", "").strip()
            phone = request.form.get("phone", "").strip()
            email = request.form.get("email", "").strip()
            device_type = request.form.get("device_type", "").strip()
            device_brand = request.form.get("device_brand", "").strip()
            device_model = request.form.get("device_model", "").strip()
            issue = request.form.get("issue_description", "").strip()

            if not all([name, phone, device_type, issue]):
                flash("Please fill in all required fields.", "error")
                return render_template("book.html")

            db = get_db()
            cursor = db.execute(
                "INSERT INTO customers (name, phone, email) VALUES (?, ?, ?)",
                (name, phone, email),
            )
            customer_id = cursor.lastrowid

            cursor = db.execute(
                """INSERT INTO repair_jobs
                   (customer_id, device_type, device_brand, device_model, issue_description)
                   VALUES (?, ?, ?, ?, ?)""",
                (customer_id, device_type, device_brand, device_model, issue),
            )
            job_id = cursor.lastrowid

            db.execute(
                "INSERT INTO status_history (job_id, new_status, note) VALUES (?, 'intake', 'Job created via booking form')",
                (job_id,),
            )
            db.commit()
            flash(f"Repair booked! Your job number is #{job_id}.", "success")
            return redirect(url_for("booking_confirmation", job_id=job_id))

        return render_template("book.html")

    @app.route("/book/confirmation/<int:job_id>")
    def booking_confirmation(job_id):
        db = get_db()
        job = db.execute(
            """
            SELECT r.*, c.name AS customer_name
            FROM repair_jobs r
            JOIN customers c ON r.customer_id = c.id
            WHERE r.id = ?
            """,
            (job_id,),
        ).fetchone()
        if not job:
            return redirect(url_for("book_repair"))
        return render_template("confirmation.html", job=job)

    # ── Customer Status Tracking ─────────────────────────────────────

    @app.route("/track")
    def track():
        return render_template("track.html")

    @app.route("/track/result")
    def track_result():
        phone = request.args.get("phone", "").strip()
        if not phone:
            return redirect(url_for("track"))

        # Normalize: strip spaces, dashes, parens for flexible matching
        digits = "".join(c for c in phone if c.isdigit())
        db = get_db()
        jobs = db.execute(
            """
            SELECT r.*, c.phone
            FROM repair_jobs r
            JOIN customers c ON r.customer_id = c.id
            WHERE replace(replace(replace(replace(c.phone, ' ', ''), '-', ''), '(', ''), ')', '') LIKE ?
            ORDER BY r.updated_at DESC
            """,
            (f"%{digits}%",),
        ).fetchall()
        return render_template("track_result.html", jobs=jobs, phone=phone)

    # ── Job Editing (costs/notes) ────────────────────────────────────

    @app.route("/job/<int:job_id>/edit", methods=["POST"])
    @login_required
    def edit_job(job_id):
        db = get_db()
        job = db.execute("SELECT * FROM repair_jobs WHERE id = ?", (job_id,)).fetchone()
        if not job:
            flash("Job not found.", "error")
            return redirect(url_for("dashboard"))

        estimated = request.form.get("estimated_cost", "").strip()
        final = request.form.get("final_cost", "").strip()
        notes = request.form.get("notes", "").strip()

        try:
            estimated_cost = float(estimated) if estimated else None
        except ValueError:
            estimated_cost = job["estimated_cost"]

        try:
            final_cost = float(final) if final else None
        except ValueError:
            final_cost = job["final_cost"]

        db.execute(
            "UPDATE repair_jobs SET estimated_cost = ?, final_cost = ?, notes = ?, updated_at = ? WHERE id = ?",
            (estimated_cost, final_cost, notes, datetime.now(), job_id),
        )
        db.commit()
        flash("Job updated.", "success")
        return redirect(url_for("job_detail", job_id=job_id))

    return app


if __name__ == "__main__":
    app = create_app()
    app.run(debug=True, host="0.0.0.0", port=5000)
