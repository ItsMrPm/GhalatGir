"""Smart exam generator.

Supports every practice mode:
  * smart        — the original composition-based review exam (newest,
                   repeated, forgotten, mastered-for-confidence).
  * educational  — تست آموزشی: filter-based selection, one booklet.
  * coverage     — تست پوششی: filter-based selection, one booklet, batch
                   check at the end.
  * custom_exam  — آزمون شخصی‌سازی‌شده: user-chosen lessons/grades/topic,
                   optional timer, one booklet.
  * full_exam    — آزمون کامل کنکوری: multiple booklets, each filled from
                   its own per-lesson specs (services/full_exam_spec.py).
"""
import json
import uuid
from datetime import datetime, timedelta, date


def _mistake_matches(m, filters):
    lesson_ids = filters.get("lesson_ids")
    if lesson_ids and m["lesson_id"] not in [int(x) for x in lesson_ids]:
        return False
    bases = filters.get("bases")
    if bases and (m.get("base") is None or int(m["base"]) not in [int(b) for b in bases]):
        return False
    difficulty_max = filters.get("difficulty_max")
    if difficulty_max and (m["difficulty"] or 5) > int(difficulty_max):
        return False
    topic = (filters.get("topic_search") or "").strip()
    if topic and topic not in (m.get("topic_name") or ""):
        return False
    chapter = (filters.get("chapter_search") or "").strip()
    if chapter and chapter not in (m.get("chapter_name") or ""):
        return False
    return True


def _load_pool(conn, user_id, include_mastered=False):
    statuses = "('active','improving')" if not include_mastered else "('active','improving','mastered')"
    rows = conn.execute(
        f"SELECT * FROM mistakes WHERE user_id=? AND status IN {statuses}",
        (user_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def _insert_exam(conn, user_id, title, mode, config, total_questions, time_limit_sec):
    exam_id = uuid.uuid4().hex
    conn.execute(
        """INSERT INTO exams (id, user_id, title, source_type, mode, config_snapshot,
           total_questions, time_limit_sec, status, progress, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?)""",
        (exam_id, user_id, title,
         "auto_generated" if mode in ("smart", "full_exam") else "manual",
         mode, json.dumps(config, ensure_ascii=False),
         total_questions, time_limit_sec,
         json.dumps({"current_booklet": 0, "finished_booklets": []}),
         datetime.utcnow().isoformat()),
    )
    return exam_id


def _insert_questions(conn, exam_id, selected):
    """selected: list of (mistake_dict, slot_category, booklet_index)."""
    for i, (m, category, booklet_index) in enumerate(selected):
        conn.execute(
            """INSERT INTO exam_questions (id, exam_id, mistake_id, order_index,
               booklet_index, slot_category) VALUES (?, ?, ?, ?, ?, ?)""",
            (uuid.uuid4().hex, exam_id, m["id"], i, booklet_index, category),
        )


# ---------------------------------------------------------------- full_exam --

def generate_full_exam(conn, user_id, spec, title=None):
    """spec = output of services.full_exam_spec.get_full_exam_spec()."""
    # A full exam may draw on mastered mistakes too, so booklets fill up as
    # much as the bank allows.
    pool = _load_pool(conn, user_id, include_mastered=True)
    pool_by_lesson = {}
    for m in pool:
        row = conn.execute("SELECT name FROM lessons WHERE id=?", (m["lesson_id"],)).fetchone()
        m["_lesson_name"] = row["name"] if row else None
        pool_by_lesson.setdefault(m["_lesson_name"], []).append(m)

    selected = []
    used_ids = set()
    booklet_meta = []

    def take_from(candidates, n, category):
        # deterministic-ish but not always identical: priority then random tie-break
        ordered = sorted(candidates, key=lambda m: (-(m["priority_score"] or 0), m["id"]))
        picked = 0
        for m in ordered:
            if picked >= n:
                break
            if m["id"] in used_ids:
                continue
            used_ids.add(m["id"])
            selected.append((m, category, b_idx))
            picked += 1
        return picked

    for b_idx, booklet in enumerate(spec["booklets"]):
        got = 0
        for s in booklet["specs"]:
            cands = []
            for name in s["lesson_names"]:
                cands += pool_by_lesson.get(name, [])
            if s["bases"]:
                cands = [m for m in cands if (m.get("base") in s["bases"])]
            cat_label = "، ".join(s["lesson_names"])
            got += take_from(cands, s["count"], cat_label)
        # top up the booklet from any of its lessons (any grade) if specs fell short
        target = booklet["total"]
        if got < target:
            union = []
            for s in booklet["specs"]:
                for name in s["lesson_names"]:
                    union += pool_by_lesson.get(name, [])
            got += take_from(union, target - got, "تکمیلی")
        booklet_meta.append({
            "index": b_idx, "name": booklet["name"],
            "time_limit_sec": booklet["time_limit_sec"],
            "planned": target,
        })

    exam_id = _insert_exam(
        conn, user_id, title or "آزمون کامل", "full_exam",
        {"mode": "full_exam", "spec": spec, "booklets": booklet_meta},
        len(selected), spec["total_time_sec"],
    )
    _insert_questions(conn, exam_id, selected)
    conn.commit()
    return exam_id


# ------------------------------------------------- filter-based single block --

def generate_filtered_exam(conn, user_id, mode, total_questions, filters,
                           time_limit_minutes=None, title=None, composition=None):
    """Used for educational / coverage / custom_exam / smart modes (one booklet)."""
    filters = filters or {}

    if mode == "smart" and composition:
        selected = _smart_select(conn, user_id, total_questions, composition, filters)
    else:
        pool = [m for m in _load_pool(conn, user_id) if _mistake_matches(m, filters)]
        pool.sort(key=lambda m: (-(m["priority_score"] or 0), m["id"]))
        selected = [(m, "selected", 0) for m in pool[:total_questions]]

    default_titles = {
        "smart": "آزمون مروری",
        "educational": "تست آموزشی",
        "coverage": "تست پوششی",
        "custom_exam": "آزمون شخصی‌سازی‌شده",
    }
    exam_id = _insert_exam(
        conn, user_id, title or default_titles.get(mode, "آزمون"),
        mode, {"mode": mode, "filters": filters, "composition": composition or {}},
        len(selected), (time_limit_minutes or 0) * 60,
    )
    _insert_questions(conn, exam_id, selected)
    conn.commit()
    return exam_id


# ------------------------------------------------------------------- legacy --

def _smart_select(conn, user_id, total_questions, composition, filters):
    """Original composition-based algorithm (newest / repeated / forgotten /
    mastered-for-confidence, topped up by priority)."""
    base = [m for m in _load_pool(conn, user_id) if _mistake_matches(m, filters)]

    selected = []
    selected_ids = set()

    def take(rows, n, category):
        added = []
        for r in rows:
            if len(added) >= n:
                break
            if r["id"] in selected_ids:
                continue
            added.append(r)
            selected_ids.add(r["id"])
            selected.append((r, category, 0))
        return added

    take(sorted([m for m in base if not m["last_reviewed_at"]],
                key=lambda m: m["created_at"], reverse=True),
         composition.get("newest", 0), "newest")

    take(sorted([m for m in base if (m["repeat_count"] or 0) >= 1],
                key=lambda m: m["priority_score"], reverse=True),
         composition.get("repeated", 0), "repeated")

    thirty_days_ago = (datetime.utcnow() - timedelta(days=30)).isoformat()
    today_str = date.today().isoformat()
    take(sorted([m for m in base
                 if (m["last_reviewed_at"] and m["last_reviewed_at"] < thirty_days_ago)
                 or (m["next_review_date"] and m["next_review_date"] < today_str)],
                key=lambda m: (m["next_review_date"] or "")),
         composition.get("forgotten", 0), "forgotten")

    mastered_pool = conn.execute(
        "SELECT * FROM mistakes WHERE user_id=? AND status='mastered' ORDER BY RANDOM()",
        (user_id,),
    ).fetchall()
    take([dict(m) for m in mastered_pool], composition.get("mastered_confidence", 0),
         "mastered_confidence")

    if len(selected) < total_questions:
        remaining = total_questions - len(selected)
        take(sorted(base, key=lambda m: m["priority_score"], reverse=True),
             remaining, "fallback")

    return selected


def generate_exam(conn, user_id, total_questions, composition, filters,
                  time_limit_minutes=None, title=None):
    """Backwards-compatible entry point (mode='smart')."""
    return generate_filtered_exam(
        conn, user_id, "smart", total_questions, filters,
        time_limit_minutes=time_limit_minutes, title=title, composition=composition,
    )
