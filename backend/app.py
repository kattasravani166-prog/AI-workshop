import os
from uuid import UUID

from dotenv import load_dotenv
from flask import Flask, jsonify, request
from postgrest import APIError
from supabase import Client, create_client

load_dotenv()

app = Flask(__name__)

supabase_url = os.environ.get("SUPABASE_URL")
supabase_key = os.environ.get("SUPABASE_PUBLISHABLE_KEY")
supabase: Client | None = (
    create_client(supabase_url, supabase_key)
    if supabase_url and supabase_key
    else None
)


@app.after_request
def enable_cors(response):
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization"
    return response


@app.route("/api/<path:_path>", methods=["OPTIONS"])
def api_options(_path):
    return "", 204


def db():
    if supabase is None:
        return None, (jsonify(error="Database is not configured"), 503)
    return supabase, None


def valid_uuid(value):
    try:
        return str(UUID(str(value)))
    except (ValueError, TypeError, AttributeError):
        return None


def database_error(error):
    return jsonify(error=getattr(error, "message", str(error))), 500


@app.get("/api/events")
def list_events():
    client, problem = db()
    if problem:
        return problem
    try:
        result = client.table("events").select("*").execute()
        return jsonify(result.data)
    except APIError as error:
        return database_error(error)


@app.get("/api/events/<event_id>")
def get_event(event_id):
    event_id = valid_uuid(event_id)
    if not event_id:
        return jsonify(error="Invalid event id"), 400
    client, problem = db()
    if problem:
        return problem
    try:
        result = client.table("events").select("*").eq("id", event_id).limit(1).execute()
        if not result.data:
            return jsonify(error="Event not found"), 404
        return jsonify(result.data[0])
    except APIError as error:
        return database_error(error)


@app.post("/api/registrations")
def create_registration():
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify(error="Expected a JSON object"), 400
    user_id = valid_uuid(payload.get("user_id"))
    event_id = valid_uuid(payload.get("event_id"))
    if not user_id or not event_id:
        return jsonify(error="Valid user_id and event_id are required"), 400

    client, problem = db()
    if problem:
        return problem
    try:
        # Duplicate (user_id, event_id) registrations are reported as conflicts.
        existing = (
            client.table("registrations")
            .select("id")
            .eq("user_id", user_id)
            .eq("event_id", event_id)
            .limit(1)
            .execute()
        )
        if existing.data:
            return jsonify(error="Already registered for this event"), 409

        event_result = client.table("events").select("id").eq("id", event_id).limit(1).execute()
        if not event_result.data:
            return jsonify(error="Event not found"), 404

        created = (
            client.table("registrations")
            .insert({"user_id": user_id, "event_id": event_id})
            .execute()
        )
        registration = created.data[0] if created.data else {"user_id": user_id, "event_id": event_id}
        return jsonify(registration), 201
    except APIError as error:
        # A database uniqueness constraint also protects against concurrent requests.
        if getattr(error, "code", None) == "23505":
            return jsonify(error="Already registered for this event"), 409
        return database_error(error)


@app.get("/api/me/registrations")
def my_registrations():
    user_id = valid_uuid(request.args.get("user_id") or request.headers.get("X-User-Id"))
    if not user_id:
        return jsonify(error="Provide a valid user_id query parameter or X-User-Id header"), 400
    client, problem = db()
    if problem:
        return problem
    try:
        result = (
            client.table("registrations")
            .select("*, events(*)")
            .eq("user_id", user_id)
            .execute()
        )
        return jsonify(result.data)
    except APIError as error:
        return database_error(error)


@app.get("/api/events/<event_id>/registrations")
def event_registrations(event_id):
    event_id = valid_uuid(event_id)
    organizer_id = valid_uuid(request.args.get("organizer_id") or request.headers.get("X-User-Id"))
    if not event_id:
        return jsonify(error="Invalid event id"), 400
    if not organizer_id:
        return jsonify(error="Provide the organizer_id query parameter or X-User-Id header"), 400
    client, problem = db()
    if problem:
        return problem
    try:
        event_result = (
            client.table("events")
            .select("id, organizer_id")
            .eq("id", event_id)
            .limit(1)
            .execute()
        )
        if not event_result.data:
            return jsonify(error="Event not found"), 404
        if event_result.data[0].get("organizer_id") != organizer_id:
            return jsonify(error="Only the event organizer can view attendees"), 403
        result = (
            client.table("registrations")
            .select("*, users(id, email, role)")
            .eq("event_id", event_id)
            .execute()
        )
        return jsonify(result.data)
    except APIError as error:
        return database_error(error)


@app.route("/")
def index():
    return jsonify(message="Events API", endpoints=[
        "GET /api/events",
        "GET /api/events/<id>",
        "POST /api/registrations",
        "GET /api/me/registrations?user_id=<uuid>",
        "GET /api/events/<id>/registrations?organizer_id=<uuid>",
    ])


if __name__ == "__main__":
    app.run(debug=True)
