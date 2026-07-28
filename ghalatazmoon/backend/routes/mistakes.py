import json
import sqlite3
import uuid
from datetime import datetime, date, timedelta
from flask import Blueprint, request, jsonify, g

from db import get_db
from auth import login_required
from utils import paginate_args, paginated_response, mistake_to_public
from services.priority import calculate_priority
from services.srs import REVIEW_INTERVALS
from services.weakness import detect_weakness_patterns

bp = Blueprint("mistakes", __name__, url_prefix="/api/v1/mistakes")

VALID_REASONS = {'conceptual', 'calculation', 'careless', 'forgotten_formula',
                  'time_management', 'misread_question', 'unknown'}
VALID_STATUSES = {'active', 'improving', 'mastered', 'archived'}
VALID_OPTIONS = {'1', '2', '3', '4'}
VALID_UNDERSTANDING = {'understood', 'partial', 'not_understood'}

# ordering key -> SQL expression (m = mistakes, l = lessons)
ORDERINGS = {
    "created_at": "m.created_at",
    "priority_score": "m.priority_score",
    "next_review_date": "COALESCE(m.next_review_date, '9999-12-31')",
    "difficulty": "m.difficulty",
    "repeat_count": "m.repeat_count",
    "question_number": "COALESCE(m.question_number, '')",
    "base": "COALESCE(m.base, 0)",
    "chapter_name": "COALESCE(m.chapter_name, '')",
    "topic_name": "COALESCE(m.topic_name, '')",
    "lesson": "COALESCE(l.name, '')",
    # وضعیت فهمیدن: نفهمیده < نصفه‌نیمه < فهمیده < بدون ثبت
    "understanding": ("CASE m.understanding WHEN 'not_understood' THEN 0 "
                      "WHEN 'partial' THEN 1 WHEN 'understood' THEN 2 ELSE -1 END"),
}


def _validate_answers(correct_answer, student_answer):
    """correct_answer and student_answer must be valid options and — the whole
    point of a *mistake* — different from each other."""
    for label, val in (("پاسخ صحیح", correct_answer), ("پاسخ شما", student_answer)):
        if val not in (None, "") and str(val) not in VALID_OPTIONS:
            return f"{label} باید یکی از گزینه‌های ۱ تا ۴ باشد."
    if (correct_answer not in (None, "") and student_answer not in (None, "")
            and str(correct_answer) == str(student_answer)):
        return ("پاسخ صحیح و پاسخ انتخابی شما یکسان است؛ گزینه‌ای که زده‌اید نمی‌تواند "
                "با گزینه‌ی درست برابر باشد. دو گزینه‌ی متفاوت انتخاب کنید.")
    return None


@bp.get("/")
@login_required
def list_mistakes():
    conn = get_db()
    q = """SELECT m.* FROM mistakes m LEFT JOIN lessons l ON l.id = m.lesson_id
           WHERE m.user_id=?"""
    params = [g.user_id]

    for field in ("lesson_id", "chapter_id", "topic_id"):
        arg = request.args.get(field.replace("_id", ""))
        if arg:
            q += f" AND m.{field}=?"
            params.append(arg)
    status = request.args.get("status")
    if status:
        q += " AND m.status=?"
        params.append(status)
    reason = request.args.get("reason")
    if reason:
        q += " AND m.reason=?"
        params.append(reason)
    base = request.args.get("base")
    if base:
        q += " AND m.base=?"
        params.append(base)
    understanding = request.args.get("understanding")
    if understanding:
        q += " AND m.understanding=?"
        params.append(understanding)
    chapter = request.args.get("chapter")
    if chapter:
        q += " AND m.chapter_name LIKE ?"
        params.append(f"%{chapter}%")
    topic = request.args.get("topic")
    if topic:
        q += " AND m.topic_name LIKE ?"
        params.append(f"%{topic}%")
    search = request.args.get("search")
    if search:
        q += (" AND (m.source_book LIKE ? OR m.student_note LIKE ? OR m.reason_note LIKE ? "
              "OR m.chapter_name LIKE ? OR m.topic_name LIKE ?)")
        like = f"%{search}%"
        params += [like, like, like, like, like]

    ordering = request.args.get("ordering", "-created_at")
    col = ordering.lstrip("-")
    if col not in ORDERINGS:
        col = "created_at"
    direction = "DESC" if ordering.startswith("-") else "ASC"

    count_row = conn.execute(f"SELECT COUNT(*) AS c FROM ({q})", params).fetchone()
    total = count_row["c"]

    page, page_size = paginate_args()
    offset = (page - 1) * page_size
    rows = conn.execute(
        f"{q} ORDER BY {ORDERINGS[col]} {direction} LIMIT ? OFFSET ?",
        params + [page_size, offset],
    ).fetchall()

    # attach lesson name for display
    lesson_rows = conn.execute("SELECT id, name, category FROM lessons").fetchall()
    lessons = {r["id"]: dict(r) for r in lesson_rows}
    conn.close()

    results = []
    for r in rows:
        d = mistake_to_public(dict(r))
        lesson = lessons.get(d.get("lesson_id")) or {}
        d["lesson_name"] = lesson.get("name")
        d["lesson_category"] = lesson.get("category")
        results.append(d)
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

    answer_error = _validate_answers(data.get("correct_answer"), data.get("student_answer"))
    if answer_error:
        return jsonify({"detail": answer_error}), 400

    mistake_id = uuid.uuid4().hex
    now = datetime.utcnow().isoformat()
    today = date.today().isoformat()

    base_grade = data.get("base")
    if base_grade not in (None, ""):
        base_grade = int(base_grade)

    priority_base = {
        "id": mistake_id, "user_id": g.user_id,
        "lesson_id": data.get("lesson_id"), "chapter_id": data.get("chapter_id"),
        "topic_id": data.get("topic_id"), "difficulty": difficulty,
        "reason": reason, "repeat_count": 0, "correct_streak": 0,
        "next_review_date": today,
    }
    priority = calculate_priority(priority_base)

    conn = get_db()
    try:
        conn.execute(
            """INSERT INTO mistakes (
                id, user_id, lesson_id, chapter_id, topic_id, base, chapter_name, topic_name,
                difficulty, source_book, question_number, correct_answer, student_answer,
                reason, reason_note, image_url, answer_image_url, student_note, teacher_note,
                tags, status, priority_score, repeat_count, correct_streak, next_review_date,
                interval_days, first_seen_at, created_at, updated_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (mistake_id, g.user_id, data.get("lesson_id"), data.get("chapter_id"), data.get("topic_id"),
             base_grade, (data.get("chapter_name") or "").strip() or None,
             (data.get("topic_name") or "").strip() or None,
             difficulty, data.get("source_book"), data.get("question_number"),
             data.get("correct_answer"), data.get("student_answer"), reason, data.get("reason_note"),
             data.get("image_url"), data.get("answer_image_url"), data.get("student_note"),
             data.get("teacher_note"), json.dumps(data.get("tags") or []), "active", priority,
             0, 0, today, 1, now, now, now),
        )
    except sqlite3.IntegrityError:
        conn.close()
        return jsonify({"detail": "ذخیره نشد: درس انتخاب‌شده معتبر نیست یا نشست شما منقضی شده است. "
                                  "صفحه را تازه‌سازی کنید و دوباره وارد شوید."}), 400

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
        """SELECT COUNT(*) c FROM (SELECT DISTINCT COALESCE(chapter_name, chapter_id) ch
           FROM mistakes WHERE user_id=? AND ch IS NOT NULL)""",
        (g.user_id,),
    ).fetchone()["c"]

    weakest = conn.execute(
        """SELECT l.name AS lesson_name,
                  COALESCE(m.chapter_name, c.name) AS chapter_name, COUNT(*) AS cnt
           FROM mistakes m
           LEFT JOIN lessons l ON l.id = m.lesson_id
           LEFT JOIN chapters c ON c.id = m.chapter_id
           WHERE m.user_id=? AND m.status IN ('active','improving')
             AND COALESCE(m.chapter_name, c.name) IS NOT NULL
           GROUP BY m.lesson_id, COALESCE(m.chapter_name, c.name)
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

    editable = ("lesson_id", "chapter_id", "topic_id", "base", "chapter_name", "topic_name",
                "difficulty", "source_book", "question_number", "correct_answer",
                "student_answer", "reason", "reason_note", "image_url", "answer_image_url",
                "student_note", "teacher_note", "status", "understanding")
    fields = {k: data[k] for k in editable if k in data}
    if "reason" in fields and fields["reason"] not in VALID_REASONS:
        conn.close()
        return jsonify({"detail": "reason نامعتبر است."}), 400
    if "status" in fields and fields["status"] not in VALID_STATUSES:
        conn.close()
        return jsonify({"detail": "status نامعتبر است."}), 400
    if "understanding" in fields and fields["understanding"] not in VALID_UNDERSTANDING:
        conn.close()
        return jsonify({"detail": "understanding نامعتبر است."}), 400
    if "tags" in data:
        fields["tags"] = json.dumps(data["tags"] or [])

    # validate the correct/student pair after the partial update is applied
    if "correct_answer" in fields or "student_answer" in fields:
        effective_correct = fields.get("correct_answer", row["correct_answer"])
        effective_student = fields.get("student_answer", row["student_answer"])
        answer_error = _validate_answers(effective_correct, effective_student)
        if answer_error:
            conn.close()
            return jsonify({"detail": answer_error}), 400

    if fields:
        fields["updated_at"] = datetime.utcnow().isoformat()
        set_clause = ", ".join(f"{k}=?" for k in fields)
        try:
            conn.execute(f"UPDATE mistakes SET {set_clause} WHERE id=?", (*fields.values(), mistake_id))
            conn.commit()
        except sqlite3.IntegrityError:
            conn.close()
            return jsonify({"detail": "ثبت نشد: درس انتخاب‌شده معتبر نیست."}), 400

    row = conn.execute("SELECT * FROM mistakes WHERE id=?", (mistake_id,)).fetchone()
    conn.close()
    return jsonify(mistake_to_public(dict(row)))


@bp.post("/<mistake_id>/understanding/")
@login_required
def set_understanding(mistake_id):
    """Educational-mode feedback: after seeing the answer sheet the student
    records how well they understood the question.

      understood      -> کاملا فهمیدم    (interval advances like a correct review)
      partial         -> نصفه‌نیمه فهمیدم  (short review soon, no progress)
      not_understood  -> اصلا نفهمیدم     (treated like a lapse: back to day 1)
    """
    data = request.get_json(force=True, silent=True) or {}
    understanding = data.get("understanding")
    if understanding not in VALID_UNDERSTANDING:
        return jsonify({"detail": "understanding نامعتبر است."}), 400

    conn = get_db()
    row = conn.execute("SELECT * FROM mistakes WHERE id=? AND user_id=?", (mistake_id, g.user_id)).fetchone()
    if not row:
        conn.close()
        return jsonify({"detail": "پیدا نشد."}), 404
    m = dict(row)
    now = datetime.utcnow().isoformat()
    today = date.today()

    interval = m.get("interval_days") or 1
    streak = m.get("correct_streak") or 0
    status = m.get("status") or "active"

    if understanding == "understood":
        idx = REVIEW_INTERVALS.index(interval) if interval in REVIEW_INTERVALS else 0
        interval = REVIEW_INTERVALS[min(idx + 1, len(REVIEW_INTERVALS) - 1)]
        streak += 1
        if interval == 60 and streak >= 2:
            status = "mastered"
        elif status == "active":
            status = "improving"
    elif understanding == "partial":
        interval = min(interval, REVIEW_INTERVALS[1])  # مرور نزدیک، بدون پیشروی
        if status == "mastered":
            status = "improving"
    else:  # not_understood
        interval = REVIEW_INTERVALS[0]
        streak = 0
        if status != "archived":
            status = "active"

    next_review = (today + timedelta(days=interval)).isoformat()
    m.update(interval_days=interval, correct_streak=streak, status=status,
             next_review_date=next_review, understanding=understanding,
             last_reviewed_at=now)
    priority = calculate_priority(m)

    conn.execute(
        """UPDATE mistakes SET understanding=?, interval_days=?, correct_streak=?, status=?,
           next_review_date=?, last_reviewed_at=?, priority_score=?, updated_at=?
           WHERE id=?""",
        (understanding, interval, streak, status, next_review, now, priority, now, mistake_id),
    )
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


@bp.get("/export/")
@login_required
def export_mistakes():
    conn = get_db()
    rows = conn.execute(
        """SELECT m.*, l.name AS lesson_name, l.field AS lesson_field
           FROM mistakes m LEFT JOIN lessons l ON l.id = m.lesson_id
           WHERE m.user_id=? ORDER BY m.created_at""", (g.user_id,)
    ).fetchall()
    conn.close()
    out = []
    for r in rows:
        d = dict(r)
        try: d["tags"] = json.loads(d.get("tags") or "[]")
        except Exception: d["tags"] = []
        out.append({k: d.get(k) for k in (
            "lesson_name","lesson_field","base","chapter_name","topic_name","difficulty",
            "source_book","question_number","correct_answer","student_answer","reason",
            "reason_note","image_url","answer_image_url","student_note","teacher_note",
            "tags","status","understanding","created_at")})
    return jsonify({"version": 1, "exported_at": datetime.utcnow().isoformat(), "count": len(out), "mistakes": out})


@bp.post("/import/")
@login_required
def import_mistakes():
    data = request.get_json(force=True, silent=True) or {}
    items = data.get("mistakes") if isinstance(data, dict) else data
    if not isinstance(items, list):
        return jsonify({"detail": "فرمت نامعتبر: لیست mistakes لازم است."}), 400
    conn = get_db()
    lesson_cache = {}
    for row in conn.execute("SELECT id,name,field FROM lessons").fetchall():
        lesson_cache[(row["name"], row["field"])] = row["id"]
    today = date.today().isoformat(); now = datetime.utcnow().isoformat()
    imported = 0; skipped = 0
    for it in items:
        try:
            if not isinstance(it, dict):
                skipped += 1; continue
            reason = it.get("reason") or "unknown"
            if reason not in VALID_REASONS: reason = "unknown"
            st = it.get("status") if it.get("status") in VALID_STATUSES else "active"
            ln, lf = it.get("lesson_name"), it.get("lesson_field")
            lesson_id = lesson_cache.get((ln, lf)) if ln and lf else None
            base = {"id":"x","user_id":g.user_id,"lesson_id":lesson_id,"difficulty":it.get("difficulty"),
                    "reason":reason,"repeat_count":0,"correct_streak":0,"next_review_date":today}
            priority = calculate_priority(base)
            mid = uuid.uuid4().hex
            conn.execute(
                """INSERT INTO mistakes (id,user_id,lesson_id,base,chapter_name,topic_name,difficulty,
                   source_book,question_number,correct_answer,student_answer,reason,reason_note,
                   image_url,answer_image_url,student_note,teacher_note,tags,status,understanding,
                   priority_score,repeat_count,correct_streak,next_review_date,interval_days,
                   first_seen_at,created_at,updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (mid, g.user_id, lesson_id, it.get("base"),
                 (it.get("chapter_name") or "").strip() or None,
                 (it.get("topic_name") or "").strip() or None,
                 it.get("difficulty"), it.get("source_book"), it.get("question_number"),
                 it.get("correct_answer"), it.get("student_answer"), reason, it.get("reason_note"),
                 it.get("image_url"), it.get("answer_image_url"), it.get("student_note"),
                 it.get("teacher_note"), json.dumps(it.get("tags") or []), st, it.get("understanding"),
                 priority, 0, 0, today, 1, now, it.get("created_at") or now, now))
            imported += 1
        except Exception:
            skipped += 1
    conn.commit(); conn.close()
    return jsonify({"imported": imported, "skipped": skipped})
