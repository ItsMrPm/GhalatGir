import json
import uuid
from datetime import datetime, date
from flask import Blueprint, request, jsonify, g

from db import get_db
from auth import login_required
from utils import mistake_to_public, parse_json_field
from services.exam_generator import generate_exam, generate_filtered_exam, generate_full_exam
from services.full_exam_spec import get_full_exam_spec
from services.srs import process_review_result
from services.weakness import detect_weakness_patterns

bp = Blueprint("exams", __name__, url_prefix="/api/v1/exams")

VALID_MODES = {"smart", "educational", "coverage", "custom_exam", "full_exam"}
# Modes that only reveal correctness at the end (no instant feedback).
SILENT_MODES = {"coverage", "custom_exam", "full_exam", "smart"}


def _exam_row(conn, exam_id, user_id):
    return conn.execute("SELECT * FROM exams WHERE id=? AND user_id=?", (exam_id, user_id)).fetchone()


def _exam_public(exam_row):
    d = dict(exam_row)
    d["config_snapshot"] = parse_json_field(d.get("config_snapshot"), {})
    d["progress"] = parse_json_field(d.get("progress"), {"current_booklet": 0, "finished_booklets": []})
    return d


def _questions_with_mistakes(conn, exam_id):
    rows = conn.execute(
        """SELECT eq.id, eq.exam_id, eq.mistake_id, eq.order_index, eq.booklet_index,
                  eq.slot_category, eq.is_flagged, eq.student_answer, eq.is_correct,
                  eq.time_spent_sec, eq.answered_at,
                  COALESCE(m.correct_answer, eq.correct_answer) AS m_correct_answer,
                  COALESCE(m.source_book, eq.source_label) AS source_book,
                  COALESCE(m.question_number, eq.qnum) AS question_number,
                  COALESCE(m.image_url, eq.image_url) AS image_url,
                  m.answer_image_url,
                  m.student_note,
                  m.teacher_note,
                  COALESCE(m.reason, 'unknown') AS reason,
                  m.lesson_id, m.chapter_id, m.topic_id,
                  COALESCE(m.base, eq.base) AS base,
                  m.chapter_name,
                  COALESCE(m.topic_name, eq.topic_name) AS topic_name,
                  m.understanding, m.difficulty,
                  COALESCE(l.name, eq.lesson_name) AS lesson_name,
                  l.category AS lesson_category,
                  eq.text AS qtext
           FROM exam_questions eq
           LEFT JOIN mistakes m ON m.id = eq.mistake_id
           LEFT JOIN lessons l ON l.id = m.lesson_id
           WHERE eq.exam_id=? ORDER BY eq.booklet_index, eq.order_index""",
        (exam_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def _booklet_layout(exam_public, questions):
    """Ordered booklet metadata with per-booklet question counts."""
    config = exam_public.get("config_snapshot") or {}
    meta = config.get("booklets")
    if not meta:
        meta = [{"index": 0, "name": "سؤالات",
                 "time_limit_sec": exam_public.get("time_limit_sec") or 0,
                 "planned": len(questions)}]
    out = []
    for b in meta:
        idx = b.get("index", 0)
        qs = [q for q in questions if (q.get("booklet_index") or 0) == idx]
        out.append({
            "index": idx,
            "name": b.get("name") or f"دفترچه {idx + 1}",
            "time_limit_sec": b.get("time_limit_sec") or 0,
            "planned": b.get("planned") or len(qs),
            "available": len(qs),
        })
    return out


@bp.post("/generate/")
@login_required
def generate():
    data = request.get_json(force=True, silent=True) or {}
    mode = data.get("mode") or "smart"
    if mode not in VALID_MODES:
        return jsonify({"detail": f"mode نامعتبر است: {mode}"}), 400

    conn = get_db()
    try:
        if mode == "full_exam":
            field = data.get("field")
            grade = data.get("grade")  # 10 | 11 | 12 | 'all'
            if field not in ("ریاضی", "تجربی"):
                conn.close()
                return jsonify({"detail": "رشته باید «ریاضی» یا «تجربی» باشد."}), 400
            if str(grade) not in ("10", "11", "12", "all"):
                conn.close()
                return jsonify({"detail": "پایه باید یکی از ۱۰، ۱۱، ۱۲ یا «هر سه پایه» باشد."}), 400
            grade = "all" if str(grade) == "all" else int(grade)
            try:
                spec = get_full_exam_spec(field, grade)
            except ValueError as e:
                conn.close()
                return jsonify({"detail": str(e)}), 400
            grade_fa = {"all": "هر سه پایه (مشابه کنکور)", 10: "دهم", 11: "یازدهم", 12: "دوازدهم"}[grade]
            title = data.get("title") or f"آزمون کامل {field} — {grade_fa}"
            exam_id = generate_full_exam(conn, g.user_id, spec, title=title)
        else:
            total_questions = int(data.get("total_questions") or 20)
            composition = data.get("composition") or {}
            filters = data.get("filters") or {}
            time_limit_minutes = data.get("time_limit_minutes")
            title = data.get("title")
            exam_id = generate_filtered_exam(
                conn, g.user_id, mode, total_questions, filters,
                time_limit_minutes=time_limit_minutes, title=title,
                composition=composition if mode == "smart" else None,
            )
    finally:
        pass

    exam = _exam_public(conn.execute("SELECT * FROM exams WHERE id=?", (exam_id,)).fetchone())
    questions = _questions_with_mistakes(conn, exam_id)
    conn.close()
    exam["booklets"] = _booklet_layout(exam, questions)
    return jsonify(exam), 201


@bp.get("/")
@login_required
def list_exams():
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM exams WHERE user_id=? ORDER BY created_at DESC", (g.user_id,)
    ).fetchall()
    conn.close()
    return jsonify([_exam_public(r) for r in rows])


@bp.get("/full-exam-spec/")
@login_required
def full_exam_spec():
    """Preview the Konkur booklet layout before generating."""
    field = request.args.get("field", "تجربی")
    grade = request.args.get("grade", "all")
    grade = "all" if str(grade) == "all" else int(grade)
    try:
        spec = get_full_exam_spec(field, grade)
    except ValueError as e:
        return jsonify({"detail": str(e)}), 400
    return jsonify(spec)


@bp.get("/<exam_id>/")
@login_required
def exam_detail(exam_id):
    conn = get_db()
    exam = _exam_row(conn, exam_id, g.user_id)
    if not exam:
        conn.close()
        return jsonify({"detail": "پیدا نشد."}), 404
    exam = _exam_public(exam)
    questions = _questions_with_mistakes(conn, exam_id)
    conn.close()
    # hide correct_answer while exam is in progress so it can't be cheated via API
    for q in questions:
        if exam["status"] != "finished":
            q.pop("m_correct_answer", None)
    exam["questions"] = questions
    exam["booklets"] = _booklet_layout(exam, questions)
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
    conn.execute(
        "UPDATE exams SET status='in_progress', started_at=COALESCE(started_at, ?) WHERE id=?",
        (now, exam_id),
    )
    conn.commit()
    conn.close()
    return jsonify({"status": "in_progress", "started_at": now})


@bp.post("/<exam_id>/questions/<qid>/answer/")
@login_required
def answer_question(exam_id, qid):
    data = request.get_json(force=True, silent=True) or {}
    student_answer = data.get("student_answer")
    time_spent_sec = data.get("time_spent_sec")

    if student_answer is not None and str(student_answer) not in {"1", "2", "3", "4"}:
        return jsonify({"detail": "پاسخ باید یکی از گزینه‌های ۱ تا ۴ باشد."}), 400

    conn = get_db()
    exam = _exam_row(conn, exam_id, g.user_id)
    if not exam:
        conn.close()
        return jsonify({"detail": "پیدا نشد."}), 404
    if exam["status"] == "finished":
        conn.close()
        return jsonify({"detail": "آزمون تمام شده است."}), 400

    eq = conn.execute(
        "SELECT * FROM exam_questions WHERE id=? AND exam_id=?", (qid, exam_id)
    ).fetchone()
    if not eq:
        conn.close()
        return jsonify({"detail": "سؤال پیدا نشد."}), 404

    mid = eq["mistake_id"]
    if mid:
        mistake = conn.execute("SELECT * FROM mistakes WHERE id=?", (mid,)).fetchone()
        correct_answer = mistake["correct_answer"]
    else:
        mistake = None
        correct_answer = eq["correct_answer"]
    is_correct = str(student_answer) == str(correct_answer)
    now = datetime.utcnow().isoformat()
    first_answer = eq["answered_at"] is None

    conn.execute(
        """UPDATE exam_questions SET student_answer=?, is_correct=?, time_spent_sec=?,
           answered_at=COALESCE(answered_at, ?) WHERE id=?""",
        (student_answer, 1 if is_correct else 0, time_spent_sec, now, qid),
    )
    conn.commit()

    mode = exam["mode"] or "smart"
    # Educational: reveal on every answer (re-pick friendly); SRS once (first answer)
    # and only for mistake-backed questions. Paper questions never touch SRS.
    if mode == "educational":
        updated = dict(mistake) if mid else None
        if mid and first_answer:
            updated = process_review_result(conn, dict(mistake), is_correct, exam_question_id=qid)
            detect_weakness_patterns(conn, g.user_id)
        conn.close()
        return jsonify({
            "recorded": True,
            "is_correct": is_correct,
            "correct_answer": correct_answer,
            "student_answer": (str(student_answer) if student_answer is not None else None),
            "mistake": (mistake_to_public(updated) if mid else None),
        })

    conn.close()
    return jsonify({"recorded": True})


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


@bp.post("/<exam_id>/booklets/<int:idx>/finish/")
@login_required
def finish_booklet(exam_id, idx):
    """Mark a booklet as done (timer expired or «پایان دفترچه» pressed) and
    move on to the next one."""
    conn = get_db()
    exam = _exam_row(conn, exam_id, g.user_id)
    if not exam:
        conn.close()
        return jsonify({"detail": "پیدا نشد."}), 404
    progress = parse_json_field(exam["progress"], {"current_booklet": 0, "finished_booklets": []})
    finished = set(progress.get("finished_booklets") or [])
    finished.add(idx)
    progress["finished_booklets"] = sorted(finished)
    progress["current_booklet"] = max(finished) + 1
    conn.execute("UPDATE exams SET progress=? WHERE id=?", (json.dumps(progress), exam_id))
    conn.commit()
    conn.close()
    return jsonify({"progress": progress})


@bp.post("/<exam_id>/finish/")
@login_required
def finish_exam(exam_id):
    conn = get_db()
    exam = _exam_row(conn, exam_id, g.user_id)
    if not exam:
        conn.close()
        return jsonify({"detail": "پیدا نشد."}), 404

    already_finished = exam["status"] == "finished"
    now = datetime.utcnow().isoformat()
    conn.execute("UPDATE exams SET status='finished', finished_at=? WHERE id=?", (now, exam_id))

    questions = _questions_with_mistakes(conn, exam_id)
    answered = [q for q in questions if q["student_answer"] is not None]
    # recompute correctness against the final (possibly changed) answer
    mistake_cache = {}
    for q in answered:
        q["is_correct"] = 1 if str(q["student_answer"]) == str(q.get("m_correct_answer")) else 0
        conn.execute("UPDATE exam_questions SET is_correct=? WHERE id=?", (q["is_correct"], q["id"]))
        if q["mistake_id"] and q["mistake_id"] not in mistake_cache:
            mistake_cache[q["mistake_id"]] = dict(conn.execute("SELECT * FROM mistakes WHERE id=?", (q["mistake_id"],)).fetchone())
    correct = [q for q in answered if q["is_correct"]]

    if not already_finished:
        mode = exam["mode"] or "smart"
        # For silent modes the SRS reschedule happens once, here, on the final
        # answer of each mistake (educational mode already did it per question).
        if mode != "educational":
            seen = set()
            for q in answered:
                if not q["mistake_id"] or q["mistake_id"] in seen:
                    continue
                seen.add(q["mistake_id"])
                m = mistake_cache[q["mistake_id"]]
                process_review_result(conn, m, bool(q["is_correct"]), exam_question_id=q["id"])
            detect_weakness_patterns(conn, g.user_id)

    # daily_stats rollup + study_sessions questions_reviewed
    today = date.today().isoformat()[:10]
    by_lesson = {}
    for q in answered:
        if not q.get("mistake_id") or not q.get("lesson_id"):
            continue
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

    # per-booklet breakdown
    booklets = _booklet_layout(_exam_public(exam), questions)
    for b in booklets:
        bqs = [q for q in answered if (q.get("booklet_index") or 0) == b["index"]]
        b["answered"] = len(bqs)
        b["correct"] = sum(1 for q in bqs if q["is_correct"])
        b["accuracy_pct"] = round(100 * b["correct"] / len(bqs), 2) if bqs else 0

    conn.close()
    return jsonify({
        "status": "finished",
        "total_questions": len(questions),
        "answered": len(answered),
        "correct": len(correct),
        "accuracy_pct": round(100 * len(correct) / len(answered), 2) if answered else 0,
        "booklets": booklets,
    })


@bp.get("/<exam_id>/result/")
@login_required
def exam_result(exam_id):
    conn = get_db()
    exam = _exam_row(conn, exam_id, g.user_id)
    if not exam:
        conn.close()
        return jsonify({"detail": "پیدا نشد."}), 404
    exam = _exam_public(exam)
    questions = _questions_with_mistakes(conn, exam_id)
    conn.close()
    answered = [q for q in questions if q["student_answer"] is not None]
    correct = [q for q in answered if q["is_correct"]]
    booklets = _booklet_layout(exam, questions)
    for b in booklets:
        bqs = [q for q in answered if (q.get("booklet_index") or 0) == b["index"]]
        b["answered"] = len(bqs)
        b["correct"] = sum(1 for q in bqs if q["is_correct"])
        b["accuracy_pct"] = round(100 * b["correct"] / len(bqs), 2) if bqs else 0
    return jsonify({
        "exam_id": exam_id,
        "mode": exam.get("mode"),
        "status": exam["status"],
        "total_questions": len(questions),
        "answered": len(answered),
        "correct": len(correct),
        "accuracy_pct": round(100 * len(correct) / len(answered), 2) if answered else 0,
        "booklets": booklets,
        "questions": questions,
    })
