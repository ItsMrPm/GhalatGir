import os, uuid
from flask import Blueprint, request, jsonify, send_from_directory, abort
from auth import login_required

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
UPLOAD_DIR = os.path.join(BASE_DIR, "uploads")
os.makedirs(UPLOAD_DIR, exist_ok=True)
ALLOWED = {"png", "jpg", "jpeg", "gif", "webp"}

bp = Blueprint("uploads", __name__)


def _allowed(name):
    return "." in name and name.rsplit(".", 1)[1].lower() in ALLOWED


@bp.post("/api/v1/uploads/")
@login_required
def upload():
    f = request.files.get("file")
    if not f or not f.filename:
        return jsonify({"detail": "فایلی ارسال نشد."}), 400
    if not _allowed(f.filename):
        return jsonify({"detail": "فقط تصویر (png/jpg/jpeg/gif/webp)."}), 400
    ext = f.filename.rsplit(".", 1)[1].lower()
    stored = uuid.uuid4().hex + "." + ext
    f.save(os.path.join(UPLOAD_DIR, stored))
    return jsonify({"url": "/uploads/" + stored}), 201


@bp.get("/uploads/<path:fn>")
def serve(fn):
    safe = os.path.basename(fn)
    if not safe or not os.path.isfile(os.path.join(UPLOAD_DIR, safe)):
        abort(404)
    return send_from_directory(UPLOAD_DIR, safe)
