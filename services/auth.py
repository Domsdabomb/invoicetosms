import functools
from hashlib import sha256
import secrets
from flask import session, redirect, url_for, flash, request
from models.database import get_db


def hash_password(password):
    """Hash a password with a random salt using SHA-256."""
    salt = secrets.token_hex(16)
    hashed = sha256((salt + password).encode()).hexdigest()
    return f"{salt}:{hashed}"


def verify_password(password, stored):
    """Verify a password against a stored salt:hash pair."""
    salt, hashed = stored.split(":", 1)
    return sha256((salt + password).encode()).hexdigest() == hashed


def get_admin(username):
    db = get_db()
    return db.execute("SELECT * FROM admins WHERE username = ?", (username,)).fetchone()


def create_admin(username, password):
    db = get_db()
    db.execute(
        "INSERT INTO admins (username, password_hash) VALUES (?, ?)",
        (username, hash_password(password)),
    )
    db.commit()


def admin_exists():
    db = get_db()
    return db.execute("SELECT COUNT(*) FROM admins").fetchone()[0] > 0


def login_required(f):
    """Decorator to protect admin routes."""
    @functools.wraps(f)
    def wrapped(*args, **kwargs):
        if "admin_id" not in session:
            flash("Please log in.", "error")
            return redirect(url_for("login", next=request.path))
        return f(*args, **kwargs)
    return wrapped
