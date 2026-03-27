from flask import Flask, render_template, request, redirect, url_for, flash
from config import Config
from models.database import get_db, close_db, init_db, VALID_STATUSES
from datetime import datetime


def create_app():
    app = Flask(__name__)
    app.config.from_object(Config)
    app.teardown_appcontext(close_db)

    with app.app_context():
        init_db()

    # ── Admin Dashboard ──────────────────────────────────────────────

    @app.route("/")
    def dashboard():
        db = get_db()
        jobs = db.execute(
            """
            SELECT r.*, c.name AS customer_name, c.phone AS customer_phone
            FROM repair_jobs r
            JOIN customers c ON r.customer_id = c.id
            ORDER BY r.updated_at DESC
            """
        ).fetchall()
        return render_template("dashboard.html", jobs=jobs, statuses=VALID_STATUSES)

    @app.route("/job/<int:job_id>")
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
        return render_template(
            "job_detail.html", job=job, history=history, statuses=VALID_STATUSES
        )

    @app.route("/job/<int:job_id>/update-status", methods=["POST"])
    def update_status(job_id):
        new_status = request.form.get("status")
        note = request.form.get("note", "")

        if new_status not in VALID_STATUSES:
            flash("Invalid status.", "error")
            return redirect(url_for("job_detail", job_id=job_id))

        db = get_db()
        current = db.execute(
            "SELECT status FROM repair_jobs WHERE id = ?", (job_id,)
        ).fetchone()
        if not current:
            flash("Job not found.", "error")
            return redirect(url_for("dashboard"))

        db.execute(
            "UPDATE repair_jobs SET status = ?, updated_at = ? WHERE id = ?",
            (new_status, datetime.now(), job_id),
        )
        db.execute(
            "INSERT INTO status_history (job_id, old_status, new_status, note) VALUES (?, ?, ?, ?)",
            (job_id, current["status"], new_status, note),
        )
        db.commit()
        flash(f"Status updated to {new_status.replace('_', ' ').title()}.", "success")
        return redirect(url_for("job_detail", job_id=job_id))

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

    return app


if __name__ == "__main__":
    app = create_app()
    app.run(debug=True, host="0.0.0.0", port=5000)
