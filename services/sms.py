import logging
from flask import current_app

log = logging.getLogger(__name__)

# Status → message template mapping
# Only statuses listed here trigger an SMS to the customer.
SMS_TEMPLATES = {
    "diagnosing": (
        "Hi {name}, your {device} (Job #{job_id}) is now being diagnosed. "
        "We'll update you once we know what's needed."
    ),
    "waiting_for_parts": (
        "Hi {name}, your {device} (Job #{job_id}) needs a part on order. "
        "We'll let you know as soon as it arrives."
    ),
    "in_repair": (
        "Hi {name}, great news — your {device} (Job #{job_id}) is now being repaired!"
    ),
    "ready_for_pickup": (
        "Hi {name}, your {device} (Job #{job_id}) is ready for pickup! "
        "Come grab it at your convenience. — {business}"
    ),
    "completed": (
        "Hi {name}, Job #{job_id} is complete. "
        "Thanks for choosing {business}!"
    ),
}


def build_message(status, name, device, job_id):
    """Build the SMS body for a given status change. Returns None if no SMS needed."""
    template = SMS_TEMPLATES.get(status)
    if not template:
        return None
    return template.format(
        name=name,
        device=device,
        job_id=job_id,
        business=current_app.config.get("BUSINESS_NAME", "The Crucible"),
    )


def send_sms(phone, message):
    """Send an SMS via Twilio. Returns True on success, False on failure."""
    account_sid = current_app.config.get("TWILIO_ACCOUNT_SID")
    auth_token = current_app.config.get("TWILIO_AUTH_TOKEN")
    from_number = current_app.config.get("TWILIO_PHONE_NUMBER")

    if not all([account_sid, auth_token, from_number]):
        log.warning("SMS not sent — Twilio credentials not configured. Message: %s", message)
        return False

    try:
        from twilio.rest import Client

        client = Client(account_sid, auth_token)
        msg = client.messages.create(body=message, from_=from_number, to=phone)
        log.info("SMS sent to %s (SID: %s)", phone, msg.sid)
        return True
    except Exception as e:
        log.error("SMS send failed to %s: %s", phone, e)
        return False


def notify_customer(job_id, new_status, customer_name, customer_phone, device_type, device_brand=None, device_model=None):
    """High-level function: build message and send SMS for a status change."""
    device = device_type
    if device_brand:
        device = f"{device_brand} {device_model or ''}".strip()

    message = build_message(new_status, customer_name, device, job_id)
    if not message:
        log.debug("No SMS template for status '%s' — skipping notification.", new_status)
        return None

    sent = send_sms(customer_phone, message)
    return {"phone": customer_phone, "message": message, "sent": sent}
