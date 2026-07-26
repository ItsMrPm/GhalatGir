"""Spaced repetition scheduler — ported from architecture.md section 6
(simplified SM-2 with the fixed interval ladder requested in the brief).
"""
from datetime import datetime, timedelta, date
from services.priority import calculate_priority

REVIEW_INTERVALS = [1, 3, 7, 14, 30, 60]


def process_review_result(conn, mistake_row: dict, was_correct: bool, exam_question_id: str = None):
    """Mutates the mistake's SRS fields, persists them, and logs review_history.
    Mirrors POST /exams/{id}/questions/{qid}/answer/ from architecture.md section 4/6.
    Returns the updated mistake dict.
    """
    m = dict(mistake_row)
    interval_before = m.get("interval_days") or 1

    if was_correct:
        m["correct_streak"] = (m.get("correct_streak") or 0) + 1
        current_index = REVIEW_INTERVALS.index(interval_before) if interval_before in REVIEW_INTERVALS else 0
        next_index = min(current_index + 1, len(REVIEW_INTERVALS) - 1)
        m["interval_days"] = REVIEW_INTERVALS[next_index]

        if m["interval_days"] == 60 and m["correct_streak"] >= 2:
            m["status"] = "mastered"
        else:
            m["status"] = "improving"
    else:
        m["correct_streak"] = 0
        m["repeat_count"] = (m.get("repeat_count") or 0) + 1
        m["interval_days"] = REVIEW_INTERVALS[0]
        m["status"] = "active"

    m["next_review_date"] = (date.today() + timedelta(days=m["interval_days"])).isoformat()
    m["last_reviewed_at"] = datetime.utcnow().isoformat()
    m["priority_score"] = calculate_priority(m)
    m["updated_at"] = datetime.utcnow().isoformat()

    conn.execute(
        """UPDATE mistakes SET correct_streak=?, repeat_count=?, interval_days=?, status=?,
           next_review_date=?, last_reviewed_at=?, priority_score=?, updated_at=?
           WHERE id=?""",
        (m["correct_streak"], m["repeat_count"], m["interval_days"], m["status"],
         m["next_review_date"], m["last_reviewed_at"], m["priority_score"], m["updated_at"], m["id"]),
    )
    conn.execute(
        """INSERT INTO review_history (mistake_id, exam_question_id, result, interval_before, interval_after, reviewed_at)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (m["id"], exam_question_id, 1 if was_correct else 0, interval_before, m["interval_days"], m["last_reviewed_at"]),
    )
    conn.commit()
    return m
