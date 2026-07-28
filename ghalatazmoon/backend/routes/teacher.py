import random, string, uuid
from datetime import datetime, date
from flask import Blueprint, request, jsonify, g
from db import get_db
from auth import login_required
from utils import mistake_to_public

bp = Blueprint("teacher", __name__, url_prefix="/api/v1")


def _code():
    return "".join(random.choices(string.ascii_uppercase + string.digits, k=6))


def _owns(conn, cid, uid):
    return conn.execute("SELECT 1 FROM classes WHERE id=? AND teacher_id=?", (cid, uid)).fetchone() is not None


def _teacher_of(conn, teacher, student):
    return conn.execute(
        "SELECT 1 FROM class_members cm JOIN classes c ON c.id=cm.class_id "
        "WHERE c.teacher_id=? AND cm.user_id=?", (teacher, student)).fetchone() is not None


def _student_stats(conn, uid):
    today = date.today().isoformat()
    total = conn.execute("SELECT COUNT(*) c FROM mistakes WHERE user_id=?", (uid,)).fetchone()["c"]
    active = conn.execute("SELECT COUNT(*) c FROM mistakes WHERE user_id=? AND status IN ('active','improving')", (uid,)).fetchone()["c"]
    mastered = conn.execute("SELECT COUNT(*) c FROM mistakes WHERE user_id=? AND status='mastered'", (uid,)).fetchone()["c"]
    due = conn.execute("SELECT COUNT(*) c FROM mistakes WHERE user_id=? AND status IN ('active','improving') "
                       "AND (next_review_date IS NULL OR next_review_date<=?)", (uid, today)).fetchone()["c"]
    rh = conn.execute("SELECT COUNT(*) t, SUM(result) c FROM review_history rh JOIN mistakes m ON m.id=rh.mistake_id "
                      "WHERE m.user_id=?", (uid,)).fetchone()
    t = rh["t"] or 0; c = rh["c"] or 0
    acc = round(100 * c / t, 2) if t else None
    streak = conn.execute("SELECT streak_count s FROM users WHERE id=?", (uid,)).fetchone()["s"] or 0
    return {"total": total, "active": active, "mastered": mastered, "due_today": due,
            "accuracy_pct": acc, "reviews": t, "streak": streak}


@bp.post("/teacher/classes/")
@login_required
def create_class():
    d = request.get_json(force=True, silent=True) or {}
    name = (d.get("name") or "").strip()
    if not name:
        return jsonify({"detail": "نام کلاس لازم است."}), 400
    conn = get_db()
    code = _code()
    for _ in range(10):
        if not conn.execute("SELECT 1 FROM classes WHERE join_code=?", (code,)).fetchone():
            break
        code = _code()
    cid = uuid.uuid4().hex
    conn.execute("INSERT INTO classes (id,teacher_id,name,join_code,created_at) VALUES (?,?,?,?,?)",
                 (cid, g.user_id, name, code, datetime.utcnow().isoformat()))
    conn.commit(); conn.close()
    return jsonify({"id": cid, "name": name, "join_code": code}), 201


@bp.get("/teacher/classes/")
@login_required
def my_classes():
    conn = get_db()
    rows = conn.execute(
        "SELECT c.*, (SELECT COUNT(*) FROM class_members cm WHERE cm.class_id=c.id) AS members "
        "FROM classes c WHERE c.teacher_id=? ORDER BY c.created_at DESC", (g.user_id,)).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


@bp.get("/classes/mine/")
@login_required
def my_memberships():
    conn = get_db()
    rows = conn.execute(
        "SELECT c.id, c.name, c.join_code, u.full_name AS teacher_name FROM class_members cm "
        "JOIN classes c ON c.id=cm.class_id JOIN users u ON u.id=c.teacher_id "
        "WHERE cm.user_id=? ORDER BY c.name", (g.user_id,)).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


@bp.post("/classes/join/")
@login_required
def join_class():
    d = request.get_json(force=True, silent=True) or {}
    code = (d.get("code") or "").strip().upper()
    if not code:
        return jsonify({"detail": "کد کلاس لازم است."}), 400
    conn = get_db()
    cls = conn.execute("SELECT * FROM classes WHERE join_code=?", (code,)).fetchone()
    if not cls:
        conn.close(); return jsonify({"detail": "کلاسی با این کد یافت نشد."}), 404
    if cls["teacher_id"] == g.user_id:
        conn.close(); return jsonify({"detail": "خودت معلم این کلاسی."}), 400
    if not conn.execute("SELECT 1 FROM class_members WHERE class_id=? AND user_id=?", (cls["id"], g.user_id)).fetchone():
        conn.execute("INSERT INTO class_members (class_id,user_id,joined_at) VALUES (?,?,?)",
                     (cls["id"], g.user_id, datetime.utcnow().isoformat()))
        conn.commit()
    conn.close()
    return jsonify({"class_id": cls["id"], "name": cls["name"]})


@bp.get("/teacher/classes/<cid>/students/")
@login_required
def class_students(cid):
    conn = get_db()
    if not _owns(conn, cid, g.user_id):
        conn.close(); return jsonify({"detail": "دسترسی ندارید."}), 403
    rows = conn.execute("SELECT u.id,u.full_name,u.grade,u.field FROM class_members cm "
                        "JOIN users u ON u.id=cm.user_id WHERE cm.class_id=? ORDER BY u.full_name", (cid,)).fetchall()
    out = []
    for r in rows:
        u = dict(r); u["stats"] = _student_stats(conn, u["id"]); out.append(u)
    conn.close()
    return jsonify(out)


@bp.get("/teacher/students/<uid>/mistakes/")
@login_required
def student_mistakes(uid):
    conn = get_db()
    if not _teacher_of(conn, g.user_id, uid):
        conn.close(); return jsonify({"detail": "دسترسی ندارید."}), 403
    rows = conn.execute("SELECT m.*, l.name AS lesson_name FROM mistakes m "
                        "LEFT JOIN lessons l ON l.id=m.lesson_id WHERE m.user_id=? ORDER BY m.created_at DESC", (uid,)).fetchall()
    conn.close()
    return jsonify([mistake_to_public(dict(r)) for r in rows])


@bp.patch("/teacher/students/<uid>/mistakes/<mid>/note/")
@login_required
def set_note(uid, mid):
    d = request.get_json(force=True, silent=True) or {}
    note = d.get("teacher_note")
    conn = get_db()
    if not _teacher_of(conn, g.user_id, uid):
        conn.close(); return jsonify({"detail": "دسترسی ندارید."}), 403
    if not conn.execute("SELECT id FROM mistakes WHERE id=? AND user_id=?", (mid, uid)).fetchone():
        conn.close(); return jsonify({"detail": "یافت نشد."}), 404
    conn.execute("UPDATE mistakes SET teacher_note=?, updated_at=? WHERE id=?",
                 (note, datetime.utcnow().isoformat(), mid))
    conn.commit(); conn.close()
    return jsonify({"ok": True})
