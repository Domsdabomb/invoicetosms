"""WSGI entrypoint for production servers (gunicorn, etc.)."""
from app import create_app

app = create_app()
