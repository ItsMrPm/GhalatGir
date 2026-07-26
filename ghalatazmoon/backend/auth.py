import os
import jwt
import uuid
from datetime import datetime, timedelta, timezone
from functools import wraps
from flask import request, jsonify, g
from werkzeug.security import generate_password_hash, check_password_hash

SECRET_KEY = os.environ.get("GHALATAZMOON_SECRET", "dev-secret-change-me")
ACCESS_TOKEN_TTL = timedelta(minutes=30)
REFRESH_TOKEN_TTL = timedelta(days=14)


def hash_password(raw: str) -> str:
    return generate_password_hash(raw)


def verify_password(raw: str, hashed: str) -> bool:
    return check_password_hash(hashed, raw)


def _issue_token(user_id: str, token_type: str, ttl: timedelta) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": user_id,
        "type": token_type,
        "iat": now,
        "exp": now + ttl,
        "jti": uuid.uuid4().hex,
    }
    return jwt.encode(payload, SECRET_KEY, algorithm="HS256")


def issue_token_pair(user_id: str) -> dict:
    return {
        "access": _issue_token(user_id, "access", ACCESS_TOKEN_TTL),
        "refresh": _issue_token(user_id, "refresh", REFRESH_TOKEN_TTL),
    }


def decode_token(token: str) -> dict:
    return jwt.decode(token, SECRET_KEY, algorithms=["HS256"])


def login_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            return jsonify({"detail": "Authentication credentials were not provided."}), 401
        token = auth_header.split(" ", 1)[1].strip()
        try:
            payload = decode_token(token)
            if payload.get("type") != "access":
                raise jwt.InvalidTokenError("wrong token type")
        except jwt.ExpiredSignatureError:
            return jsonify({"detail": "Token expired."}), 401
        except jwt.InvalidTokenError:
            return jsonify({"detail": "Invalid token."}), 401
        g.user_id = payload["sub"]
        return fn(*args, **kwargs)
    return wrapper
