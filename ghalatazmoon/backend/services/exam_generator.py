"""Smart exam generator — ported from architecture.md section 5.

Picks a mix of: newest mistakes, repeated ones, forgotten ones and a few
already-mastered ones for confidence, then tops up with highest-priority
active mistakes if a category came up short.
"""
import json
import uuid
from datetime import datetime, timedelta, date


def _apply_filters(mistakes, filters):
    lesson_ids = filters.get("lesson_ids")
    difficulty_max = filters.get("difficulty_max")
    out = []
    for m in mistakes:
        if lesson_ids and m["lesson_id"] not in lesson_ids:
            continue
        if difficulty_max and (m["difficulty"] or 5) > difficulty_max:
            continue
        out.append(m)
    return out


def generate_exam(conn, user_id: str, total_questions: int, composition: dict, filters: dict,
                   time_limit_minutes: int = None, title: str = None):
    filters = filters or {}
    composition = composition or {}

    base = conn.execute(
        "SELECT * FROM mistakes WHERE user_id=? AND status IN ('active','improving')",
        (user_id,),
    ).fetchall()
    base = _apply_filters([dict(m) for m in base], filters)

    selected = []
    selected_ids = set()

    def take(rows, n):
        added = []
        for r in rows:
            if len(added) >= n:
                break
            if r["id"] in selected_ids:
                continue
            added.append(r)
            selected_ids.add(r["id"])
        return added

    # 1) جدیدترین‌ها: تازه ثبت شده و هنوز مرور نشده
    newest_pool = sorted(
        [m for m in base if not m["last_reviewed_at"]],
        key=lambda m: m["created_at"], reverse=True,
    )
    newest = take(newest_pool, composition.get("newest", 0))
    selected += [(m, "newest") for m in newest]

    # 2) تکراری‌ها: repeat_count بالا
    repeated_pool = sorted(
        [m for m in base if (m["repeat_count"] or 0) >= 1],
        key=lambda m: m["priority_score"], reverse=True,
    )
    repeated = take(repeated_pool, composition.get("repeated", 0))
    selected += [(m, "repeated") for m in repeated]

    # 3) فراموش‌شده‌ها
    thirty_days_ago = (datetime.utcnow() - timedelta(days=30)).isoformat()
    today_str = date.today().isoformat()
    forgotten_pool = sorted(
        [m for m in base if (m["last_reviewed_at"] and m["last_reviewed_at"] < thirty_days_ago)
         or (m["next_review_date"] and m["next_review_date"] < today_str)],
        key=lambda m: (m["next_review_date"] or ""),
    )
    forgotten = take(forgotten_pool, composition.get("forgotten", 0))
    selected += [(m, "forgotten") for m in forgotten]

    # 4) اعتمادبه‌نفس: چند مورد مسلط‌شده
    mastered_pool = conn.execute(
        "SELECT * FROM mistakes WHERE user_id=? AND status='mastered' ORDER BY RANDOM()",
        (user_id,),
    ).fetchall()
    confidence = take([dict(m) for m in mastered_pool], composition.get("mastered_confidence", 0))
    selected += [(m, "mastered_confidence") for m in confidence]

    # اگر ترکیب کامل نشد، از بقیه‌ی active با بالاترین priority پر کن
    if len(selected) < total_questions:
        remaining = total_questions - len(selected)
        fallback_pool = sorted(base, key=lambda m: m["priority_score"], reverse=True)
        fallback = take(fallback_pool, remaining)
        selected += [(m, "fallback") for m in fallback]

    exam_id = uuid.uuid4().hex
    conn.execute(
        """INSERT INTO exams (id, user_id, title, source_type, config_snapshot,
           total_questions, time_limit_sec, status, created_at)
           VALUES (?, ?, ?, 'auto_generated', ?, ?, ?, 'pending', ?)""",
        (exam_id, user_id, title or "آزمون مروری", json.dumps({"composition": composition, "filters": filters}),
         len(selected), (time_limit_minutes or 0) * 60, datetime.utcnow().isoformat()),
    )
    for i, (m, category) in enumerate(selected):
        conn.execute(
            """INSERT INTO exam_questions (id, exam_id, mistake_id, order_index, slot_category)
               VALUES (?, ?, ?, ?, ?)""",
            (uuid.uuid4().hex, exam_id, m["id"], i, category),
        )
    conn.commit()
    return exam_id
