"""
Cloud Function — Pub/Sub Event Processor
Triggered by: carbon-events topic

Handles:
- footprint_analyzed  → update leaderboard + check achievements
- action_completed    → award points + streak tracking
- weekly_digest_requested → generate + send WhatsApp/email digest
- carbon_spike_alert  → send alert if CO2 > threshold

Deploy: gcloud functions deploy carbon-event-processor \
        --runtime python311 --trigger-topic carbon-events \
        --region asia-south1
"""

import json
import base64
import os
from datetime import datetime, timezone, timedelta

import functions_framework
from google.cloud import firestore
from google.cloud import secretmanager
from vertexai.generative_models import GenerativeModel
import vertexai

# Twilio for WhatsApp
from twilio.rest import Client as TwilioClient


# ─── Init ─────────────────────────────────────────────────────────────────────
PROJECT_ID = os.environ.get("GOOGLE_CLOUD_PROJECT", "carbon-nexus-prod")

vertexai.init(project=PROJECT_ID, location="asia-south1")
db     = firestore.Client(project=PROJECT_ID)
gemini = GenerativeModel("gemini-2.0-flash-001")


def _get_secret(secret_id: str) -> str:
    """Fetch secret from Secret Manager"""
    client = secretmanager.SecretManagerServiceClient()
    name   = f"projects/{PROJECT_ID}/secrets/{secret_id}/versions/latest"
    resp   = client.access_secret_version(request={"name": name})
    return resp.payload.data.decode("UTF-8")


# ─── Main Cloud Function Entry Point ─────────────────────────────────────────

@functions_framework.cloud_event
def process_carbon_event(cloud_event):
    """
    Pub/Sub push trigger.
    Message data is base64-encoded JSON.
    """
    raw     = base64.b64decode(cloud_event.data["message"]["data"]).decode()
    event   = json.loads(raw)
    etype   = event.get("event_type")
    user_id = event.get("user_id")

    print(f"Processing event: {etype} for user: {user_id}")

    handlers = {
        "footprint_analyzed":        handle_footprint_analyzed,
        "action_completed":          handle_action_completed,
        "weekly_digest_requested":   handle_weekly_digest,
        "carbon_spike_alert":        handle_carbon_spike,
    }

    handler = handlers.get(etype)
    if handler:
        handler(event)
    else:
        print(f"Unknown event type: {etype}")


# ─── Event Handlers ───────────────────────────────────────────────────────────

def handle_footprint_analyzed(event: dict):
    """
    After footprint calculated:
    1. Update leaderboard score in Firestore
    2. Check if CO2 spiked vs last month → trigger alert
    3. Award XP points
    """
    user_id    = event["user_id"]
    co2        = event.get("co2", 0)
    green_score = max(0, int(100 - (co2 / 0.545) * 30))  # 0.545t = India monthly avg

    # Update leaderboard
    user_ref  = db.collection("users").document(user_id)
    user_data = user_ref.get().to_dict() or {}
    location  = user_data.get("location", "Belagavi")

    db.collection("leaderboard").document(user_id).set({
        "user_id":     user_id,
        "display_name": user_data.get("display_name", "Anonymous"),
        "location":    location,
        "green_score": green_score,
        "monthly_co2": co2,
        "updated_at":  datetime.now(timezone.utc).isoformat(),
        "avatar":      user_data.get("avatar", ""),
    }, merge=True)

    # Check for CO2 spike (>20% above last month)
    history_ref = user_ref.collection("monthly_history")\
                           .order_by("month", direction=firestore.Query.DESCENDING)\
                           .limit(2)
    history = [h.to_dict() for h in history_ref.stream()]
    if len(history) == 2:
        last_co2 = history[1].get("total_co2", co2)
        if co2 > last_co2 * 1.20:
            publish_spike_alert(user_id, co2, last_co2, user_data)

    # Award XP
    user_ref.set({"xp_points": firestore.Increment(10)}, merge=True)
    print(f"✅ Leaderboard updated for {user_id}: score={green_score}, co2={co2}t")


def handle_action_completed(event: dict):
    """
    Action marked done:
    1. Award XP (10 base + streak multiplier)
    2. Update streak counter
    3. Check achievement unlocks
    """
    user_id   = event["user_id"]
    action_id = event.get("action_id", "")
    user_ref  = db.collection("users").document(user_id)
    user_data = user_ref.get().to_dict() or {}

    # Calculate streak bonus
    last_action = user_data.get("last_action_date", "")
    today       = datetime.now(timezone.utc).date().isoformat()
    yesterday   = (datetime.now(timezone.utc).date() - timedelta(days=1)).isoformat()

    streak = user_data.get("action_streak", 0)
    if last_action == yesterday:
        streak += 1
    elif last_action != today:
        streak = 1

    xp_earned = 10 * min(streak, 5)   # max 5x multiplier

    user_ref.set({
        "xp_points":        firestore.Increment(xp_earned),
        "action_streak":    streak,
        "last_action_date": today,
        "total_actions":    firestore.Increment(1),
    }, merge=True)

    # Check achievements
    total_actions = user_data.get("total_actions", 0) + 1
    check_achievements(user_id, streak, total_actions)

    print(f"✅ Action {action_id} completed: +{xp_earned} XP, streak={streak}")


def handle_weekly_digest(event: dict):
    """
    Generate weekly digest via Gemini + send via WhatsApp (Twilio) or email.
    """
    user_id  = event["user_id"]
    user_ref = db.collection("users").document(user_id)
    user     = user_ref.get().to_dict() or {}

    # Get this month's footprint
    month_key   = datetime.now(timezone.utc).strftime("%Y-%m")
    history_ref = user_ref.collection("monthly_history").document(month_key)
    history     = history_ref.get().to_dict() or {}

    co2        = history.get("total_co2", 0)
    green_score = user.get("green_score", 50)
    streak     = user.get("action_streak", 0)
    actions_done = user.get("total_actions", 0)

    # Gemini-generated digest message
    prompt = f"""
    Generate a friendly weekly carbon footprint digest for a WhatsApp message.
    
    User: {user.get('display_name', 'there')}
    Location: {user.get('location', 'Belagavi, Karnataka')}
    This month's CO2: {co2:.1f} tonnes
    Green score: {green_score}/100
    Actions completed: {actions_done}
    Current streak: {streak} days
    
    Format as WhatsApp message (use emojis, max 150 words).
    Include: 1 achievement highlight, 1 specific tip for next week, green score trend.
    End with an encouraging call to action.
    """
    response = gemini.generate_content(prompt)
    message  = response.text

    # Send via WhatsApp (Twilio)
    phone = user.get("whatsapp_phone", "")
    if phone:
        _send_whatsapp(phone, message)

    # Log digest sent
    user_ref.collection("digests").add({
        "month":      month_key,
        "message":    message,
        "sent_at":    datetime.now(timezone.utc).isoformat(),
        "channel":    "whatsapp" if phone else "skipped",
    })

    print(f"✅ Weekly digest sent for {user_id}")


def handle_carbon_spike(event: dict):
    """
    CO2 spiked >20% vs last month → send alert via WhatsApp
    """
    user_id  = event["user_id"]
    user     = db.collection("users").document(user_id).get().to_dict() or {}
    co2      = event.get("co2", 0)
    last_co2 = event.get("last_co2", 0)
    pct_up   = round((co2 - last_co2) / last_co2 * 100)

    prompt = f"""
    Write a short, caring WhatsApp alert (max 80 words, with emojis) telling
    {user.get('display_name', 'the user')} that their carbon footprint went up
    {pct_up}% this month ({last_co2:.1f}t → {co2:.1f}t).
    
    Suggest ONE quick win they can do TODAY in {user.get('location', 'their city')}.
    Keep tone encouraging, not alarming.
    """
    response = gemini.generate_content(prompt)
    message  = response.text

    phone = user.get("whatsapp_phone", "")
    if phone:
        _send_whatsapp(phone, message)

    print(f"✅ Spike alert sent for {user_id}: {last_co2}t → {co2}t (+{pct_up}%)")


# ─── Helpers ──────────────────────────────────────────────────────────────────

def publish_spike_alert(user_id: str, co2: float, last_co2: float, user: dict):
    from google.cloud import pubsub_v1
    publisher  = pubsub_v1.PublisherClient()
    topic_path = publisher.topic_path(PROJECT_ID, "carbon-events")
    message    = json.dumps({
        "event_type": "carbon_spike_alert",
        "user_id":    user_id,
        "co2":        co2,
        "last_co2":   last_co2,
        "timestamp":  datetime.now(timezone.utc).isoformat(),
    }).encode()
    publisher.publish(topic_path, message)


def check_achievements(user_id: str, streak: int, total_actions: int):
    """Unlock achievement badges stored in Firestore"""
    achievements = []
    if streak >= 7:   achievements.append({"id": "week_streak",    "name": "7-day streak! 🔥"})
    if streak >= 30:  achievements.append({"id": "month_streak",   "name": "Month warrior! 🏆"})
    if total_actions >= 10: achievements.append({"id": "action_10", "name": "10 actions done! 🌱"})
    if total_actions >= 50: achievements.append({"id": "action_50", "name": "Carbon champion! 🌍"})

    if achievements:
        user_ref = db.collection("users").document(user_id)
        for badge in achievements:
            user_ref.collection("achievements").document(badge["id"]).set({
                **badge,
                "unlocked_at": datetime.now(timezone.utc).isoformat()
            }, merge=True)


def _send_whatsapp(to_number: str, message: str):
    """Send WhatsApp message via Twilio"""
    try:
        account_sid = _get_secret("twilio-account-sid")
        auth_token  = _get_secret("twilio-auth-token")
        from_number = _get_secret("twilio-whatsapp-number")

        client = TwilioClient(account_sid, auth_token)
        client.messages.create(
            from_=f"whatsapp:{from_number}",
            to=f"whatsapp:{to_number}",
            body=message
        )
        print(f"✅ WhatsApp sent to {to_number[:6]}***")
    except Exception as e:
        print(f"❌ WhatsApp send failed: {e}")