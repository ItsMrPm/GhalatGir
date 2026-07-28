"""Repeat-pattern detection — ported from architecture.md section 7.3.
Groups active mistakes by (lesson, topic, reason); once the count crosses a
threshold, records a WeaknessPattern the dashboard can surface as a nudge.
Topics are the free-text ones the student types (m.topic_name), with a
fallback to the legacy curriculum topics table.
"""
import uuid
from datetime import datetime

THRESHOLD = 3


def detect_weakness_patterns(conn, user_id: str):
    rows = conn.execute(
        """SELECT m.lesson_id,
                  COALESCE(NULLIF(m.topic_name, ''), t.name) AS topic_name,
                  m.topic_id,
                  m.reason, COUNT(*) AS cnt
           FROM mistakes m LEFT JOIN topics t ON t.id = m.topic_id
           WHERE m.user_id=? AND m.status IN ('active','improving')
             AND COALESCE(NULLIF(m.topic_name, ''), t.name) IS NOT NULL
           GROUP BY m.lesson_id, COALESCE(NULLIF(m.topic_name, ''), t.name), m.reason
           HAVING cnt >= ?""",
        (user_id, THRESHOLD),
    ).fetchall()

    created = []
    for r in rows:
        existing = conn.execute(
            """SELECT id FROM weakness_patterns
               WHERE user_id=? AND topic_id IS ? AND reason=? AND dismissed=0
                 AND message LIKE ?""",
            (user_id, r["topic_id"], r["reason"], f"%{r['topic_name']}%"),
        ).fetchone()
        reason_fa = {
            'conceptual': 'مفهومی', 'calculation': 'محاسباتی', 'careless': 'بی‌دقتی',
            'forgotten_formula': 'فراموشی فرمول', 'time_management': 'مدیریت زمان',
            'misread_question': 'بدخوانی سؤال', 'unknown': 'نامشخص',
        }.get(r["reason"], r["reason"])
        message = f"{r['cnt']} غلط {reason_fa} در «{r['topic_name']}» — پیشنهاد می‌شود مبحث را مرور کنی."
        if existing:
            conn.execute(
                "UPDATE weakness_patterns SET mistake_count=?, message=? WHERE id=?",
                (r["cnt"], message, existing["id"]),
            )
        else:
            pid = uuid.uuid4().hex
            conn.execute(
                """INSERT INTO weakness_patterns (id, user_id, topic_id, reason, mistake_count, message, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (pid, user_id, r["topic_id"], r["reason"], r["cnt"], message, datetime.utcnow().isoformat()),
            )
            created.append(pid)
    conn.commit()
    return created
