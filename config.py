import os

BASE_DIR = os.path.abspath(os.path.dirname(__file__))


class Config:
    SECRET_KEY = os.environ.get("SECRET_KEY", "crucible-dev-key-change-me")
    DATABASE = os.path.join(BASE_DIR, "crucible.db")
    BUSINESS_NAME = "The Crucible"
    BUSINESS_REGION = "British Columbia"
