import uuid
from datetime import datetime
from flask import Blueprint, request, jsonify, g

from db import get_db
from auth import hash_password, verify_password, issue_token_pair, decode_token, login_required

bp = Blueprint("auth_routes", __name__, url_prefix="/api/v1/auth")

# NOTE: architecture.md specifies OTP-over-SMS registration/login. Sending
# real SMS needs an external gateway this sandbox can't reach, so this MVP
# uses phone_number + password instead. Swapping in an OTP provider later
# only touches this file — the rest of the app is unaffected.


@bp.post("/register/")
def register():
    data = request.get_json(force=True, silent=True) or {}
    phone = (data.get("phone_number") or "").strip()
    password = data.get("password") or ""
    full_name = (data.get("full_name") or "").strip()

    if not phone or not password or not full_name:
        return jsonify({"detail": "phone_number, password و full_name الزامی‌اند."}), 400
    if len(password) < 6:
        return jsonify({"detail": "رمز عبور باید حداقل ۶ کاراکتر باشد."}), 400

    conn = get_db()
    existing = conn.execute("SELECT id FROM users WHERE phone_number=?", (phone,)).fetchone()
    if existing:
        conn.close()
        return jsonify({"detail": "این شماره موبایل قبلاً ثبت‌نام کرده است."}), 409

    user_id = uuid.uuid4().hex
    now = datetime.utcnow().isoformat()
    conn.execute(
        """INSERT INTO users (id, phone_number, email, full_name, grade, field,
           target_konkur_year, password_hash, streak_count, longest_streak,
           last_active_date, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, 0, ?, ?, ?)""",
        (user_id, phone, data.get("email"), full_name, data.get("grade"), data.get("field"),
         data.get("target_konkur_year"), hash_password(password), now[:10], now, now),
    )
    conn.commit()
    tokens = issue_token_pair(user_id)
    conn.close()
    return jsonify({"user_id": user_id, **tokens}), 201


@bp.post("/login/")
def login():
    data = request.get_json(force=True, silent=True) or {}
    phone = (data.get("phone_number") or "").strip()
    password = data.get("password") or ""

    conn = get_db()
    user = conn.execute("SELECT * FROM users WHERE phone_number=?", (phone,)).fetchone()
    if not user or not verify_password(password, user["password_hash"]):
        conn.close()
        return jsonify({"detail": "شماره موبایل یا رمز عبور اشتباه است."}), 401

    _update_streak(conn, dict(user))
    tokens = issue_token_pair(user["id"])
    conn.close()
    return jsonify(tokens)


@bp.post("/refresh/")
def refresh():
    data = request.get_json(force=True, silent=True) or {}
    token = data.get("refresh")
    if not token:
        return jsonify({"detail": "refresh token لازم است."}), 400
    try:
        payload = decode_token(token)
        if payload.get("type") != "refresh":
            raise ValueError()
    except Exception:
        return jsonify({"detail": "refresh token نامعتبر یا منقضی است."}), 401
    return jsonify(issue_token_pair(payload["sub"]))


@bp.get("/me/")
@login_required
def me():
    conn = get_db()
    user = conn.execute("SELECT * FROM users WHERE id=?", (g.user_id,)).fetchone()
    conn.close()
    if not user:
        return jsonify({"detail": "not found"}), 404
    return jsonify(_public_user(dict(user)))


@bp.patch("/me/")
@login_required
def update_me():
    data = request.get_json(force=True, silent=True) or {}
    fields = {}
    for key in ("full_name", "email", "grade", "field", "target_konkur_year",
                "avatar_url", "theme_preference"):
        if key in data:
            fields[key] = data[key]
    if not fields:
        return jsonify({"detail": "چیزی برای به‌روزرسانی ارسال نشده."}), 400

    conn = get_db()
    fields["updated_at"] = datetime.utcnow().isoformat()
    set_clause = ", ".join(f"{k}=?" for k in fields)
    conn.execute(f"UPDATE users SET {set_clause} WHERE id=?", (*fields.values(), g.user_id))
    conn.commit()
    user = conn.execute("SELECT * FROM users WHERE id=?", (g.user_id,)).fetchone()
    conn.close()
    return jsonify(_public_user(dict(user)))


def _public_user(u: dict) -> dict:
    u = dict(u)
    u.pop("password_hash", None)
    return u


def _update_streak(conn, user: dict):
    """Simplified daily-streak bump on login, mirroring users.streak_count /
    longest_streak / last_active_date from architecture.md."""
    today = datetime.utcnow().date()
    last = user.get("last_active_date")
    if last:
        last_date = datetime.strptime(last, "%Y-%m-%d").date()
        gap = (today - last_date).days
    else:
        gap = None

    if gap == 0:
        return
    elif gap == 1:
        new_streak = (user.get("streak_count") or 0) + 1
    else:
        new_streak = 1
    longest = max(user.get("longest_streak") or 0, new_streak)
    conn.execute(
        "UPDATE users SET streak_count=?, longest_streak=?, last_active_date=? WHERE id=?",
        (new_streak, longest, today.isoformat(), user["id"]),
    )
    conn.commit()
