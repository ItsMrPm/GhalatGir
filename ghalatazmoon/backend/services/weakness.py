"""Repeat-pattern detection — ported from architecture.md section 7.3.
Groups active mistakes by (topic, reason); once the count crosses a
threshold, records a WeaknessPattern the dashboard can surface as a nudge.
"""
import uuid
from datetime import datetime

THRESHOLD = 3


def detect_weakness_patterns(conn, user_id: str):
    rows = conn.execute(
        """SELECT topic_id, reason, COUNT(*) AS cnt, t.name AS topic_name
           FROM mistakes m JOIN topics t ON t.id = m.topic_id
           WHERE m.user_id=? AND m.status IN ('active','improving') AND m.topic_id IS NOT NULL
           GROUP BY m.topic_id, m.reason
           HAVING cnt >= ?""",
        (user_id, THRESHOLD),
    ).fetchall()

    created = []
    for r in rows:
        existing = conn.execute(
            """SELECT id FROM weakness_patterns
               WHERE user_id=? AND topic_id=? AND reason=? AND dismissed=0""",
            (user_id, r["topic_id"], r["reason"]),
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
