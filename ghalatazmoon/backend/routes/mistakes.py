import json
import uuid
from datetime import datetime, date
from flask import Blueprint, request, jsonify, g

from db import get_db
from auth import login_required
from utils import paginate_args, paginated_response, mistake_to_public
from services.priority import calculate_priority
from services.weakness import detect_weakness_patterns

bp = Blueprint("mistakes", __name__, url_prefix="/api/v1/mistakes")

VALID_REASONS = {'conceptual', 'calculation', 'careless', 'forgotten_formula',
                  'time_management', 'misread_question', 'unknown'}
VALID_STATUSES = {'active', 'improving', 'mastered', 'archived'}


@bp.get("/")
@login_required
def list_mistakes():
    conn = get_db()
    q = "SELECT * FROM mistakes WHERE user_id=?"
    params = [g.user_id]

    for field in ("lesson_id", "chapter_id", "topic_id"):
        arg = request.args.get(field.replace("_id", ""))
        if arg:
            q += f" AND {field}=?"
            params.append(arg)
    status = request.args.get("status")
    if status:
        q += " AND status=?"
        params.append(status)
    reason = request.args.get("reason")
    if reason:
        q += " AND reason=?"
        params.append(reason)
    search = request.args.get("search")
    if search:
        q += " AND (source_book LIKE ? OR student_note LIKE ? OR reason_note LIKE ?)"
        like = f"%{search}%"
        params += [like, like, like]

    ordering = request.args.get("ordering", "-created_at")
    col = ordering.lstrip("-")
    allowed_cols = {"created_at", "priority_score", "next_review_date", "difficulty"}
    if col not in allowed_cols:
        col = "created_at"
    direction = "DESC" if ordering.startswith("-") else "ASC"

    count_row = conn.execute(f"SELECT COUNT(*) AS c FROM ({q})", params).fetchone()
    total = count_row["c"]

    page, page_size = paginate_args()
    offset = (page - 1) * page_size
    rows = conn.execute(
        f"{q} ORDER BY {col} {direction} LIMIT ? OFFSET ?", params + [page_size, offset]
    ).fetchall()
    conn.close()

    results = [mistake_to_public(dict(r)) for r in rows]
    return jsonify(paginated_response(total, results, page, page_size))


@bp.post("/")
@login_required
def create_mistake():
    data = request.get_json(force=True, silent=True) or {}
    reason = data.get("reason") or "unknown"
    if reason not in VALID_REASONS:
        return jsonify({"detail": f"reason نامعتبر است: {reason}"}), 400
    difficulty = data.get("difficulty")
    if difficulty is not None and not (1 <= int(difficulty) <= 5):
        return jsonify({"detail": "difficulty باید بین ۱ تا ۵ باشد."}), 400

    mistake_id = uuid.uuid4().hex
    now = datetime.utcnow().isoformat()
    today = date.today().isoformat()

    base = {
        "id": mistake_id, "user_id": g.user_id,
        "lesson_id": data.get("lesson_id"), "chapter_id": data.get("chapter_id"),
        "topic_id": data.get("topic_id"), "difficulty": difficulty,
        "reason": reason, "repeat_count": 0, "correct_streak": 0,
        "next_review_date": today,
    }
    priority = calculate_priority(base)

    conn = get_db()
    conn.execute(
        """INSERT INTO mistakes (
            id, user_id, lesson_id, chapter_id, topic_id, difficulty, source_book,
            question_number, correct_answer, student_answer, reason, reason_note,
            image_url, student_note, teacher_note, tags, status, priority_score,
            repeat_count, correct_streak, next_review_date, interval_days,
            first_seen_at, created_at, updated_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (mistake_id, g.user_id, data.get("lesson_id"), data.get("chapter_id"), data.get("topic_id"),
         difficulty, data.get("source_book"), data.get("question_number"),
         data.get("correct_answer"), data.get("student_answer"), reason, data.get("reason_note"),
         data.get("image_url"), data.get("student_note"), data.get("teacher_note"),
         json.dumps(data.get("tags") or []), "active", priority, 0, 0, today, 1,
         now, now, now),
    )

    # study_sessions rollup (mistakes_added) — mirrors architecture.md's streak/session tracking
    conn.execute(
        """INSERT INTO study_sessions (user_id, session_date, mistakes_added, questions_reviewed, duration_sec)
           VALUES (?, ?, 1, 0, 0)
           ON CONFLICT(user_id, session_date)
           DO UPDATE SET mistakes_added = mistakes_added + 1""",
        (g.user_id, today[:10]),
    )
    conn.commit()

    detect_weakness_patterns(conn, g.user_id)

    row = conn.execute("SELECT * FROM mistakes WHERE id=?", (mistake_id,)).fetchone()
    conn.close()
    return jsonify(mistake_to_public(dict(row))), 201


@bp.get("/due-today/")
@login_required
def due_today():
    conn = get_db()
    today = date.today().isoformat()
    rows = conn.execute(
        """SELECT * FROM mistakes WHERE user_id=? AND status IN ('active','improving')
           AND (next_review_date IS NULL OR next_review_date <= ?)
           ORDER BY priority_score DESC""",
        (g.user_id, today),
    ).fetchall()
    conn.close()
    return jsonify([mistake_to_public(dict(r)) for r in rows])


@bp.get("/stats-summary/")
@login_required
def stats_summary():
    conn = get_db()
    today = date.today().isoformat()

    due_today_count = conn.execute(
        """SELECT COUNT(*) c FROM mistakes WHERE user_id=? AND status IN ('active','improving')
           AND (next_review_date IS NULL OR next_review_date <= ?)""",
        (g.user_id, today),
    ).fetchone()["c"]

    overdue_count = conn.execute(
        """SELECT COUNT(*) c FROM mistakes WHERE user_id=? AND status IN ('active','improving')
           AND next_review_date < ?""",
        (g.user_id, today),
    ).fetchone()["c"]

    active_count = conn.execute(
        "SELECT COUNT(*) c FROM mistakes WHERE user_id=? AND status IN ('active','improving')",
        (g.user_id,),
    ).fetchone()["c"]

    mastered_count = conn.execute(
        "SELECT COUNT(*) c FROM mistakes WHERE user_id=? AND status='mastered'",
        (g.user_id,),
    ).fetchone()["c"]

    total_count = conn.execute(
        "SELECT COUNT(*) c FROM mistakes WHERE user_id=?", (g.user_id,)
    ).fetchone()["c"]

    lessons_touched = conn.execute(
        "SELECT COUNT(DISTINCT lesson_id) c FROM mistakes WHERE user_id=?", (g.user_id,)
    ).fetchone()["c"]
    chapters_touched = conn.execute(
        "SELECT COUNT(DISTINCT chapter_id) c FROM mistakes WHERE user_id=?", (g.user_id,)
    ).fetchone()["c"]

    weakest = conn.execute(
        """SELECT l.name AS lesson_name, c.name AS chapter_name, COUNT(*) AS cnt
           FROM mistakes m
           JOIN lessons l ON l.id = m.lesson_id
           JOIN chapters c ON c.id = m.chapter_id
           WHERE m.user_id=? AND m.status IN ('active','improving')
           GROUP BY m.lesson_id, m.chapter_id
           ORDER BY cnt DESC LIMIT 1""",
        (g.user_id,),
    ).fetchone()

    patterns = conn.execute(
        "SELECT COUNT(*) c FROM weakness_patterns WHERE user_id=? AND dismissed=0",
        (g.user_id,),
    ).fetchone()["c"]

    conn.close()
    return jsonify({
        "due_today": due_today_count,
        "overdue": overdue_count,
        "active_mistakes": active_count,
        "mastered_mistakes": mastered_count,
        "total_mistakes": total_count,
        "lessons_touched": lessons_touched,
        "chapters_touched": chapters_touched,
        "weakest_chapter": {
            "lesson": weakest["lesson_name"], "chapter": weakest["chapter_name"], "count": weakest["cnt"],
        } if weakest else None,
        "weakness_patterns": patterns,
    })


@bp.get("/<mistake_id>/")
@login_required
def get_mistake(mistake_id):
    conn = get_db()
    row = conn.execute("SELECT * FROM mistakes WHERE id=? AND user_id=?", (mistake_id, g.user_id)).fetchone()
    conn.close()
    if not row:
        return jsonify({"detail": "پیدا نشد."}), 404
    return jsonify(mistake_to_public(dict(row)))


@bp.patch("/<mistake_id>/")
@login_required
def update_mistake(mistake_id):
    data = request.get_json(force=True, silent=True) or {}
    conn = get_db()
    row = conn.execute("SELECT * FROM mistakes WHERE id=? AND user_id=?", (mistake_id, g.user_id)).fetchone()
    if not row:
        conn.close()
        return jsonify({"detail": "پیدا نشد."}), 404

    editable = ("lesson_id", "chapter_id", "topic_id", "difficulty", "source_book",
                "question_number", "correct_answer", "student_answer", "reason",
                "reason_note", "image_url", "student_note", "teacher_note", "status")
    fields = {k: data[k] for k in editable if k in data}
    if "reason" in fields and fields["reason"] not in VALID_REASONS:
        conn.close()
        return jsonify({"detail": "reason نامعتبر است."}), 400
    if "status" in fields and fields["status"] not in VALID_STATUSES:
        conn.close()
        return jsonify({"detail": "status نامعتبر است."}), 400
    if "tags" in data:
        fields["tags"] = json.dumps(data["tags"] or [])

    if fields:
        fields["updated_at"] = datetime.utcnow().isoformat()
        set_clause = ", ".join(f"{k}=?" for k in fields)
        conn.execute(f"UPDATE mistakes SET {set_clause} WHERE id=?", (*fields.values(), mistake_id))
        conn.commit()

    row = conn.execute("SELECT * FROM mistakes WHERE id=?", (mistake_id,)).fetchone()
    conn.close()
    return jsonify(mistake_to_public(dict(row)))


@bp.delete("/<mistake_id>/")
@login_required
def delete_mistake(mistake_id):
    conn = get_db()
    row = conn.execute("SELECT id FROM mistakes WHERE id=? AND user_id=?", (mistake_id, g.user_id)).fetchone()
    if not row:
        conn.close()
        return jsonify({"detail": "پیدا نشد."}), 404
    # soft-delete, matching "حذف/آرشیو" from architecture.md
    conn.execute("UPDATE mistakes SET status='archived', updated_at=? WHERE id=?",
                 (datetime.utcnow().isoformat(), mistake_id))
    conn.commit()
    conn.close()
    return "", 204
