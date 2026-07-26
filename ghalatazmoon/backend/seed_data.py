"""Seed data for the curriculum hierarchy (Lesson > Chapter > Topic).
A small but realistic slice of the Konkur (Iranian university entrance exam)
curriculum, enough to demo mistakes/exams/analytics end-to-end.
"""

CURRICULUM = [
    {
        "name": "شیمی", "field": "تجربی", "grade": 12,
        "chapters": [
            {"name": "ترمودینامیک", "topics": ["قانون هس", "آنتالپی و آنتروپی", "انرژی آزاد گیبس"]},
            {"name": "سینتیک شیمیایی", "topics": ["سرعت واکنش", "عوامل مؤثر بر سرعت", "کاتالیزگر"]},
            {"name": "تعادل شیمیایی", "topics": ["ثابت تعادل", "اصل لوشاتلیه"]},
        ],
    },
    {
        "name": "فیزیک", "field": "تجربی", "grade": 12,
        "chapters": [
            {"name": "نوسان و موج", "topics": ["حرکت هماهنگ ساده", "موج مکانیکی", "تشدید"]},
            {"name": "الکتریسیته ساکن", "topics": ["قانون کولن", "میدان الکتریکی", "پتانسیل الکتریکی"]},
        ],
    },
    {
        "name": "ریاضی", "field": "تجربی", "grade": 12,
        "chapters": [
            {"name": "حد و پیوستگی", "topics": ["حد در بی‌نهایت", "پیوستگی تابع"]},
            {"name": "مشتق", "topics": ["قواعد مشتق‌گیری", "کاربرد مشتق"]},
        ],
    },
    {
        "name": "زیست‌شناسی", "field": "تجربی", "grade": 12,
        "chapters": [
            {"name": "تقسیم یاخته", "topics": ["میتوز", "میوز"]},
            {"name": "ژنتیک", "topics": ["قوانین مندل", "کروموزوم‌های جنسی"]},
        ],
    },
]


def seed_curriculum(conn):
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) AS c FROM lessons")
    if cur.fetchone()["c"] > 0:
        return  # already seeded

    for l_idx, lesson in enumerate(CURRICULUM):
        cur.execute(
            "INSERT INTO lessons (name, field, grade, order_index) VALUES (?, ?, ?, ?)",
            (lesson["name"], lesson["field"], lesson["grade"], l_idx),
        )
        lesson_id = cur.lastrowid
        for c_idx, chapter in enumerate(lesson["chapters"]):
            cur.execute(
                "INSERT INTO chapters (lesson_id, name, order_index) VALUES (?, ?, ?)",
                (lesson_id, chapter["name"], c_idx),
            )
            chapter_id = cur.lastrowid
            for t_idx, topic_name in enumerate(chapter["topics"]):
                cur.execute(
                    "INSERT INTO topics (chapter_id, name, order_index) VALUES (?, ?, ?)",
                    (chapter_id, topic_name, t_idx),
                )
    conn.commit()
