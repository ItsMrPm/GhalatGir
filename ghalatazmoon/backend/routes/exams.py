import json
import uuid
from datetime import datetime, date
from flask import Blueprint, request, jsonify, g

from db import get_db
from auth import login_required
from utils import mistake_to_public
from services.exam_generator import generate_exam
from services.srs import process_review_result
from services.weakness import detect_weakness_patterns

bp = Blueprint("exams", __name__, url_prefix="/api/v1/exams")


def _exam_row(conn, exam_id, user_id):
    return conn.execute("SELECT * FROM exams WHERE id=? AND user_id=?", (exam_id, user_id)).fetchone()


def _questions_with_mistakes(conn, exam_id):
    rows = conn.execute(
        """SELECT eq.*, m.correct_answer AS m_correct_answer, m.source_book, m.question_number,
                  m.image_url, m.student_note, m.reason, m.lesson_id, m.chapter_id, m.topic_id,
                  m.difficulty
           FROM exam_questions eq JOIN mistakes m ON m.id = eq.mistake_id
           WHERE eq.exam_id=? ORDER BY eq.order_index""",
        (exam_id,),
    ).fetchall()
    return [dict(r) for r in rows]


@bp.post("/generate/")
@login_required
def generate():
    data = request.get_json(force=True, silent=True) or {}
    total_questions = int(data.get("total_questions") or 20)
    composition = data.get("composition") or {}
    filters = data.get("filters") or {}
    time_limit_minutes = data.get("time_limit_minutes")
    title = data.get("title")

    conn = get_db()
    exam_id = generate_exam(conn, g.user_id, total_questions, composition, filters,
                             time_limit_minutes=time_limit_minutes, title=title)
    exam = dict(conn.execute("SELECT * FROM exams WHERE id=?", (exam_id,)).fetchone())
    exam["config_snapshot"] = json.loads(exam["config_snapshot"] or "{}")
    conn.close()
    return jsonify(exam), 201


@bp.get("/")
@login_required
def list_exams():
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM exams WHERE user_id=? ORDER BY created_at DESC", (g.user_id,)
    ).fetchall()
    conn.close()
    out = []
    for r in rows:
        d = dict(r)
        d["config_snapshot"] = json.loads(d["config_snapshot"] or "{}")
        out.append(d)
    return jsonify(out)


@bp.get("/<exam_id>/")
@login_required
def exam_detail(exam_id):
    conn = get_db()
    exam = _exam_row(conn, exam_id, g.user_id)
    if not exam:
        conn.close()
        return jsonify({"detail": "پیدا نشد."}), 404
    exam = dict(exam)
    exam["config_snapshot"] = json.loads(exam["config_snapshot"] or "{}")
    questions = _questions_with_mistakes(conn, exam_id)
    conn.close()
    # hide correct_answer while exam is in progress so it can't be cheated via API
    for q in questions:
        if exam["status"] != "finished":
            q.pop("m_correct_answer", None)
    exam["questions"] = questions
    return jsonify(exam)


@bp.post("/<exam_id>/start/")
@login_required
def start_exam(exam_id):
    conn = get_db()
    exam = _exam_row(conn, exam_id, g.user_id)
    if not exam:
        conn.close()
        return jsonify({"detail": "پیدا نشد."}), 404
    now = datetime.utcnow().isoformat()
    conn.execute("UPDATE exams SET status='in_progress', started_at=? WHERE id=?", (now, exam_id))
    conn.commit()
    conn.close()
    return jsonify({"status": "in_progress", "started_at": now})


@bp.post("/<exam_id>/questions/<qid>/answer/")
@login_required
def answer_question(exam_id, qid):
    data = request.get_json(force=True, silent=True) or {}
    student_answer = data.get("student_answer")
    time_spent_sec = data.get("time_spent_sec")

    conn = get_db()
    exam = _exam_row(conn, exam_id, g.user_id)
    if not exam:
        conn.close()
        return jsonify({"detail": "پیدا نشد."}), 404

    eq = conn.execute(
        "SELECT * FROM exam_questions WHERE id=? AND exam_id=?", (qid, exam_id)
    ).fetchone()
    if not eq:
        conn.close()
        return jsonify({"detail": "سؤال پیدا نشد."}), 404

    mistake = conn.execute("SELECT * FROM mistakes WHERE id=?", (eq["mistake_id"],)).fetchone()
    is_correct = str(student_answer) == str(mistake["correct_answer"])
    now = datetime.utcnow().isoformat()

    conn.execute(
        """UPDATE exam_questions SET student_answer=?, is_correct=?, time_spent_sec=?, answered_at=?
           WHERE id=?""",
        (student_answer, 1 if is_correct else 0, time_spent_sec, now, qid),
    )
    conn.commit()

    # This is exactly the hook described in architecture.md section 6: every
    # answer to a mistake-derived question immediately reschedules its SRS.
    updated_mistake = process_review_result(conn, dict(mistake), is_correct, exam_question_id=qid)
    detect_weakness_patterns(conn, g.user_id)
    conn.close()

    return jsonify({
        "is_correct": is_correct,
        "correct_answer": mistake["correct_answer"],
        "mistake": mistake_to_public(updated_mistake),
    })


@bp.post("/<exam_id>/questions/<qid>/flag/")
@login_required
def flag_question(exam_id, qid):
    conn = get_db()
    eq = conn.execute(
        """SELECT eq.* FROM exam_questions eq JOIN exams e ON e.id = eq.exam_id
           WHERE eq.id=? AND eq.exam_id=? AND e.user_id=?""",
        (qid, exam_id, g.user_id),
    ).fetchone()
    if not eq:
        conn.close()
        return jsonify({"detail": "پیدا نشد."}), 404
    new_flag = 0 if eq["is_flagged"] else 1
    conn.execute("UPDATE exam_questions SET is_flagged=? WHERE id=?", (new_flag, qid))
    conn.commit()
    conn.close()
    return jsonify({"is_flagged": bool(new_flag)})


@bp.post("/<exam_id>/finish/")
@login_required
def finish_exam(exam_id):
    conn = get_db()
    exam = _exam_row(conn, exam_id, g.user_id)
    if not exam:
        conn.close()
        return jsonify({"detail": "پیدا نشد."}), 404

    now = datetime.utcnow().isoformat()
    conn.execute("UPDATE exams SET status='finished', finished_at=? WHERE id=?", (now, exam_id))

    # daily_stats rollup + study_sessions questions_reviewed, mirroring architecture.md's
    # rollup tables so analytics never has to aggregate review_history directly.
    questions = _questions_with_mistakes(conn, exam_id)
    answered = [q for q in questions if q["answered_at"]]
    correct = [q for q in answered if q["is_correct"]]
    today = date.today().isoformat()[:10]

    by_lesson = {}
    for q in answered:
        by_lesson.setdefault(q["lesson_id"], []).append(q)
    for lesson_id, qs in by_lesson.items():
        c = sum(1 for x in qs if x["is_correct"])
        acc = round(100 * c / len(qs), 2) if qs else 0
        conn.execute(
            """INSERT INTO daily_stats (user_id, stat_date, lesson_id, mistakes_added, mistakes_mastered, accuracy_pct)
               VALUES (?, ?, ?, 0, 0, ?)
               ON CONFLICT(user_id, stat_date, lesson_id)
               DO UPDATE SET accuracy_pct=excluded.accuracy_pct""",
            (g.user_id, today, lesson_id, acc),
        )

    conn.execute(
        """INSERT INTO study_sessions (user_id, session_date, mistakes_added, questions_reviewed, duration_sec)
           VALUES (?, ?, 0, ?, 0)
           ON CONFLICT(user_id, session_date)
           DO UPDATE SET questions_reviewed = questions_reviewed + excluded.questions_reviewed""",
        (g.user_id, today, len(answered)),
    )
    conn.commit()
    conn.close()

    return jsonify({
        "status": "finished",
        "total_questions": len(questions),
        "answered": len(answered),
        "correct": len(correct),
        "accuracy_pct": round(100 * len(correct) / len(answered), 2) if answered else 0,
    })


@bp.get("/<exam_id>/result/")
@login_required
def exam_result(exam_id):
    conn = get_db()
    exam = _exam_row(conn, exam_id, g.user_id)
    if not exam:
        conn.close()
        return jsonify({"detail": "پیدا نشد."}), 404
    questions = _questions_with_mistakes(conn, exam_id)
    conn.close()
    answered = [q for q in questions if q["answered_at"]]
    correct = [q for q in answered if q["is_correct"]]
    return jsonify({
        "exam_id": exam_id,
        "status": exam["status"],
        "total_questions": len(questions),
        "answered": len(answered),
        "correct": len(correct),
        "accuracy_pct": round(100 * len(correct) / len(answered), 2) if answered else 0,
        "questions": questions,
    })
