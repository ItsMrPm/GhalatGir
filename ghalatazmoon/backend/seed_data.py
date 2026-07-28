"""Seed data for the curriculum — the fixed list of Konkur lessons.

The old version shipped hard-coded per-lesson chapters/topics, but those
lists were wrong and incomplete. Now only the *lessons* are fixed (exactly
the list agreed with the product owner, grouped by category per field);
grade (پایه), chapter (فصل) and topic (مبحث) are free text entered by the
student when registering a mistake, because a Konkur student practices
questions from all three grades.
"""

# (name, category, grade) — grade is the official grade of the textbook.
LESSONS_BY_FIELD = {
    "ریاضی": [
        # دسته شیمی
        ("شیمی 1", "شیمی", 10),
        ("شیمی 2", "شیمی", 11),
        ("شیمی 3", "شیمی", 12),
        # دسته ریاضیات
        ("ریاضی 1", "ریاضیات", 10),
        ("حسابان 1", "ریاضیات", 11),
        ("حسابان 2", "ریاضیات", 12),
        ("آمار و احتمال", "ریاضیات", 11),
        ("گسسته", "ریاضیات", 12),
        # دسته هندسه
        ("هندسه 1", "هندسه", 10),
        ("هندسه 2", "هندسه", 11),
        ("هندسه 3", "هندسه", 12),
        # دسته فیزیک
        ("فیزیک 1", "فیزیک", 10),
        ("فیزیک 2", "فیزیک", 11),
        ("فیزیک 3", "فیزیک", 12),
    ],
    "تجربی": [
        # دسته زیست
        ("زیست 1", "زیست", 10),
        ("زیست 2", "زیست", 11),
        ("زیست 3", "زیست", 12),
        # دسته شیمی
        ("شیمی 1", "شیمی", 10),
        ("شیمی 2", "شیمی", 11),
        ("شیمی 3", "شیمی", 12),
        # دسته فیزیک
        ("فیزیک 1", "فیزیک", 10),
        ("فیزیک 2", "فیزیک", 11),
        ("فیزیک 3", "فیزیک", 12),
        # دسته ریاضی
        ("ریاضی 1", "ریاضی", 10),
        ("ریاضی 2", "ریاضی", 11),
        ("ریاضی 3", "ریاضی", 12),
        # دسته زمین‌شناسی
        ("زمین‌شناسی", "زمین‌شناسی", 11),
    ],
}

# Stable category ordering for the UI's optgroups.
CATEGORY_ORDER = {
    "ریاضی": ["ریاضیات", "هندسه", "فیزیک", "شیمی"],
    "تجربی": ["زیست", "فیزیک", "شیمی", "ریاضی", "زمین‌شناسی"],
}


def all_canonical_keys():
    keys = set()
    for field, rows in LESSONS_BY_FIELD.items():
        for name, _cat, _grade in rows:
            keys.add((name, field))
    return keys


def seed_curriculum(conn):
    """Idempotent: inserts any canonical lesson that is missing and removes
    legacy lessons (from the old hard-coded curriculum) that nothing
    references anymore."""
    cur = conn.cursor()

    canonical = all_canonical_keys()

    # Remove old/legacy lessons that are not in the canonical list, but only
    # if no mistake references them (FK-safe).
    existing = cur.execute("SELECT id, name, field FROM lessons").fetchall()
    for row in existing:
        if (row[1], row[2]) not in canonical:
            used = cur.execute(
                "SELECT COUNT(*) AS c FROM mistakes WHERE lesson_id=?", (row[0],)
            ).fetchone()[0]
            if used == 0:
                cur.execute("DELETE FROM topics WHERE chapter_id IN "
                            "(SELECT id FROM chapters WHERE lesson_id=?)", (row[0],))
                cur.execute("DELETE FROM chapters WHERE lesson_id=?", (row[0],))
                cur.execute("DELETE FROM lessons WHERE id=?", (row[0],))

    # Insert missing canonical lessons.
    for field, rows in LESSONS_BY_FIELD.items():
        cat_order = CATEGORY_ORDER.get(field, [])
        ordered = sorted(
            enumerate(rows),
            key=lambda t: (cat_order.index(t[1][1]) if t[1][1] in cat_order else 99, t[0]),
        )
        for order_index, (_orig_idx, (name, category, grade)) in enumerate(ordered):
            found = cur.execute(
                "SELECT id FROM lessons WHERE name=? AND field=?", (name, field)
            ).fetchone()
            if found:
                # keep category/grade up to date
                cur.execute(
                    "UPDATE lessons SET category=?, grade=?, order_index=? WHERE id=?",
                    (category, grade, order_index, found[0]),
                )
            else:
                cur.execute(
                    "INSERT INTO lessons (name, field, grade, category, order_index) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (name, field, grade, category, order_index),
                )
    conn.commit()
