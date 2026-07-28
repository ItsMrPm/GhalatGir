from datetime import date, timedelta
from flask import Blueprint, request, jsonify, g

from db import get_db
from auth import login_required

bp = Blueprint("analytics", __name__, url_prefix="/api/v1/analytics")


@bp.get("/overview/")
@login_required
def overview():
    conn = get_db()
    total_active = conn.execute(
        "SELECT COUNT(*) c FROM mistakes WHERE user_id=? AND status IN ('active','improving')",
        (g.user_id,),
    ).fetchone()["c"]
    mastered = conn.execute(
        "SELECT COUNT(*) c FROM mistakes WHERE user_id=? AND status='mastered'", (g.user_id,)
    ).fetchone()["c"]
    reviewed_total = conn.execute(
        """SELECT COUNT(*) c FROM review_history rh JOIN mistakes m ON m.id = rh.mistake_id
           WHERE m.user_id=?""", (g.user_id,),
    ).fetchone()["c"]
    correct_total = conn.execute(
        """SELECT COUNT(*) c FROM review_history rh JOIN mistakes m ON m.id = rh.mistake_id
           WHERE m.user_id=? AND rh.result=1""", (g.user_id,),
    ).fetchone()["c"]
    user = conn.execute("SELECT streak_count, longest_streak FROM users WHERE id=?", (g.user_id,)).fetchone()
    conn.close()
    return jsonify({
        "active_mistakes": total_active,
        "mastered_mistakes": mastered,
        "total_reviews": reviewed_total,
        "overall_accuracy_pct": round(100 * correct_total / reviewed_total, 2) if reviewed_total else None,
        "streak_count": user["streak_count"] if user else 0,
        "longest_streak": user["longest_streak"] if user else 0,
    })


@bp.get("/by-lesson/")
@login_required
def by_lesson():
    conn = get_db()
    rows = conn.execute(
        """SELECT l.id AS lesson_id, l.name AS lesson_name, COUNT(*) AS mistake_count,
                  SUM(CASE WHEN m.status='mastered' THEN 1 ELSE 0 END) AS mastered_count
           FROM mistakes m JOIN lessons l ON l.id = m.lesson_id
           WHERE m.user_id=?
           GROUP BY l.id ORDER BY mistake_count DESC""",
        (g.user_id,),
    ).fetchall()
    reason_rows = conn.execute(
        """SELECT lesson_id, reason, COUNT(*) AS cnt FROM mistakes
           WHERE user_id=? GROUP BY lesson_id, reason""",
        (g.user_id,),
    ).fetchall()
    conn.close()

    reasons_by_lesson = {}
    for r in reason_rows:
        reasons_by_lesson.setdefault(r["lesson_id"], {})[r["reason"]] = r["cnt"]

    out = []
    for r in rows:
        d = dict(r)
        d["reasons"] = reasons_by_lesson.get(d["lesson_id"], {})
        out.append(d)
    return jsonify(out)


@bp.get("/by-chapter/")
@login_required
def by_chapter():
    lesson_id = request.args.get("lesson_id")
    conn = get_db()
    # Chapters are the free-text ones the student types (m.chapter_name),
    # with a fallback to the legacy curriculum chapters table.
    q = """SELECT COALESCE(NULLIF(m.chapter_name, ''), c.name) AS chapter_name,
                  COUNT(*) AS mistake_count, AVG(m.priority_score) AS avg_priority
           FROM mistakes m LEFT JOIN chapters c ON c.id = m.chapter_id
           WHERE m.user_id=?
             AND COALESCE(NULLIF(m.chapter_name, ''), c.name) IS NOT NULL"""
    params = [g.user_id]
    if lesson_id:
        q += " AND m.lesson_id=?"
        params.append(lesson_id)
    q += (" GROUP BY COALESCE(NULLIF(m.chapter_name, ''), c.name)"
          " ORDER BY mistake_count DESC")
    rows = conn.execute(q, params).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


@bp.get("/trend/")
@login_required
def trend():
    range_param = request.args.get("range", "30d")
    try:
        days = int(range_param.rstrip("d"))
    except ValueError:
        days = 30
    since = (date.today() - timedelta(days=days)).isoformat()

    conn = get_db()
    rows = conn.execute(
        """SELECT rh.reviewed_at, rh.result FROM review_history rh
           JOIN mistakes m ON m.id = rh.mistake_id
           WHERE m.user_id=? AND rh.reviewed_at >= ?
           ORDER BY rh.reviewed_at""",
        (g.user_id, since),
    ).fetchall()
    conn.close()

    by_day = {}
    for r in rows:
        d = r["reviewed_at"][:10]
        by_day.setdefault(d, {"total": 0, "correct": 0})
        by_day[d]["total"] += 1
        by_day[d]["correct"] += 1 if r["result"] else 0

    series = [
        {"date": d, "accuracy_pct": round(100 * v["correct"] / v["total"], 2), "reviews": v["total"]}
        for d, v in sorted(by_day.items())
    ]
    return jsonify(series)


@bp.get("/reason-breakdown/")
@login_required
def reason_breakdown():
    conn = get_db()
    rows = conn.execute(
        """SELECT reason, COUNT(*) AS cnt FROM mistakes WHERE user_id=?
           GROUP BY reason ORDER BY cnt DESC""",
        (g.user_id,),
    ).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


@bp.get("/weak-topics/")
@login_required
def weak_topics():
    conn = get_db()
    # Topics are the free-text ones the student types (m.topic_name), with a
    # fallback to the legacy curriculum topics table.
    rows = conn.execute(
        """SELECT COALESCE(NULLIF(m.topic_name, ''), t.name) AS topic_name,
                  COALESCE(NULLIF(m.chapter_name, ''), c.name) AS chapter_name,
                  l.name AS lesson_name, COUNT(*) AS mistake_count, AVG(m.priority_score) AS avg_priority
           FROM mistakes m
           LEFT JOIN topics t ON t.id = m.topic_id
           LEFT JOIN chapters c ON c.id = m.chapter_id
           LEFT JOIN lessons l ON l.id = m.lesson_id
           WHERE m.user_id=? AND m.status IN ('active','improving')
             AND COALESCE(NULLIF(m.topic_name, ''), t.name) IS NOT NULL
           GROUP BY COALESCE(NULLIF(m.topic_name, ''), t.name), m.lesson_id
           ORDER BY avg_priority DESC LIMIT 10""",
        (g.user_id,),
    ).fetchall()

    patterns = conn.execute(
        "SELECT * FROM weakness_patterns WHERE user_id=? AND dismissed=0 ORDER BY mistake_count DESC",
        (g.user_id,),
    ).fetchall()
    conn.close()
    return jsonify({
        "weak_topics": [dict(r) for r in rows],
        "weakness_patterns": [dict(r) for r in patterns],
    })


@bp.get("/activity/")
@login_required
def activity():
    try: days = max(7, min(400, int(request.args.get("days", 120))))
    except ValueError: days = 120
    conn = get_db()
    rows = conn.execute(
        """SELECT session_date, COALESCE(mistakes_added,0)+COALESCE(questions_reviewed,0) AS c
           FROM study_sessions WHERE user_id=? AND session_date >= date('now', ?)""",
        (g.user_id, "-%d days" % days)
    ).fetchall()
    conn.close()
    by = {r["session_date"]: r["c"] for r in rows}
    out = []
    d0 = date.today()
    for i in range(days-1, -1, -1):
        d = (d0 - timedelta(days=i)).isoformat()
        out.append({"date": d, "count": by.get(d, 0)})
    return jsonify(out)


@bp.get("/reason-trend/")
@login_required
def reason_trend():
    conn = get_db()
    rows = conn.execute(
        """SELECT substr(created_at,1,10) AS d, reason, COUNT(*) AS c
           FROM mistakes WHERE user_id=? AND created_at >= datetime('now','-90 days')
           GROUP BY d, reason ORDER BY d""", (g.user_id,)
    ).fetchall()
    conn.close()
    buckets = {}
    for r in rows:
        try: d = date.fromisoformat(r["d"])
        except Exception: continue
        week = (d - timedelta(days=d.weekday())).isoformat()
        b = buckets.setdefault(week, {"week_start": week, "total": 0, "reasons": {}})
        b["reasons"][r["reason"]] = b["reasons"].get(r["reason"], 0) + r["c"]
        b["total"] += r["c"]
    weeks = sorted(buckets.values(), key=lambda x: x["week_start"])[-8:]
    return jsonify(weeks)
