import os

BASE_DIR = os.path.abspath(os.path.dirname(__file__))


class Config:
    SECRET_KEY = os.environ.get("SECRET_KEY", "crucible-dev-key-change-me")
    DATABASE = os.path.join(BASE_DIR, "crucible.db")
    BUSINESS_NAME = "The Crucible"
    BUSINESS_REGION = "British Columbia"

    # Twilio SMS — set these in .env or environment variables
    TWILIO_ACCOUNT_SID = os.environ.get("TWILIO_ACCOUNT_SID")
    TWILIO_AUTH_TOKEN = os.environ.get("TWILIO_AUTH_TOKEN")
    TWILIO_PHONE_NUMBER = os.environ.get("TWILIO_PHONE_NUMBER")  # e.g. +1250XXXXXXX
