"""دفترچه‌های آزمون کامل — exactly like the Konkur booklets.

Each field/grade combination defines an ordered list of booklets; every
booklet has its own time budget and its own question composition
(per-lesson counts). Exams start booklet-by-booklet: the student can move
between questions *inside* a booklet, and the next booklet only starts
when the current one's time runs out or the student ends it manually.
"""

# A spec item: which lessons (by exact lesson name), which grades, how many.
def _item(names, count, bases=None):
    return {"lesson_names": list(names), "count": count, "bases": list(bases or [])}


ALL_BASES = [10, 11, 12]


def _riazi_all():
    return [
        {
            "name": "دفترچه ۱ — ریاضیات",
            "time_limit_sec": 70 * 60,
            "specs": [
                _item(["هندسه 1", "هندسه 2", "هندسه 3", "آمار و احتمال", "گسسته"], 26, ALL_BASES),
                _item(["حسابان 1", "حسابان 2", "ریاضی 1"], 14, ALL_BASES),
            ],
        },
        {
            "name": "دفترچه ۲ — فیزیک و شیمی",
            "time_limit_sec": 75 * 60,
            "specs": [
                _item(["فیزیک 1"], 10, [10]),
                _item(["فیزیک 2"], 10, [11]),
                _item(["فیزیک 3"], 15, [12]),
                _item(["شیمی 1"], 10, [10]),
                _item(["شیمی 2"], 10, [11]),
                _item(["شیمی 3"], 10, [12]),
            ],
        },
    ]


def _riazi_single(grade):
    if grade == 10:
        math_specs = [_item(["ریاضی 1"], 20, [10]), _item(["هندسه 1"], 10, [10])]
    elif grade == 11:
        math_specs = [_item(["حسابان 1"], 20, [11]),
                      _item(["آمار و احتمال"], 5, [11]),
                      _item(["هندسه 2"], 5, [11])]
    else:  # 12
        math_specs = [_item(["حسابان 2"], 12, [12]),
                      _item(["گسسته"], 10, [12]),
                      _item(["هندسه 3"], 8, [12])]
    return [
        {"name": "دفترچه ۱ — ریاضیات", "time_limit_sec": 55 * 60, "specs": math_specs},
        {
            "name": "دفترچه ۲ — فیزیک و شیمی",
            "time_limit_sec": 45 * 60,
            "specs": [_item([f"فیزیک {grade}"], 20, [grade]),
                      _item([f"شیمی {grade}"], 20, [grade])],
        },
    ]


def _tajrobi_all():
    return [
        {
            "name": "دفترچه ۱ — زیست‌شناسی",
            "time_limit_sec": 45 * 60,
            "specs": [
                _item(["زیست 1"], 15, [10]),
                _item(["زیست 2"], 15, [11]),
                _item(["زیست 3"], 15, [12]),
            ],
        },
        {
            "name": "دفترچه ۲ — فیزیک و شیمی",
            "time_limit_sec": 75 * 60,
            "specs": [
                _item(["فیزیک 1"], 8, [10]),
                _item(["فیزیک 2"], 9, [11]),
                _item(["فیزیک 3"], 13, [12]),
                _item(["شیمی 1"], 11, [10]),
                _item(["شیمی 2"], 12, [11]),
                _item(["شیمی 3"], 12, [12]),
            ],
        },
        {
            "name": "دفترچه ۳ — ریاضی و زمین‌شناسی",
            "time_limit_sec": 60 * 60,
            "specs": [
                _item(["ریاضی 1"], 10, [10]),
                _item(["ریاضی 2"], 10, [11]),
                _item(["ریاضی 3"], 10, [12]),
                _item(["زمین‌شناسی"], 15, [11, 12]),
            ],
        },
    ]


def _tajrobi_single(grade):
    booklets = [
        {
            "name": "دفترچه ۱ — زیست‌شناسی",
            "time_limit_sec": 30 * 60,
            "specs": [_item([f"زیست {grade}"], 30, [grade])],
        },
        {
            "name": "دفترچه ۲ — فیزیک و شیمی",
            "time_limit_sec": 45 * 60,
            "specs": [_item([f"فیزیک {grade}"], 20, [grade]),
                      _item([f"شیمی {grade}"], 20, [grade])],
        },
    ]
    if grade == 10:
        math_specs = [_item(["ریاضی 1"], 20, [10])]
    elif grade == 11:
        math_specs = [_item(["ریاضی 2"], 20, [11]), _item(["زمین‌شناسی"], 10, [11])]
    else:  # 12
        math_specs = [_item(["ریاضی 3"], 20, [12])]
    booklets.append({
        "name": "دفترچه ۳ — ریاضی و زمین‌شناسی",
        "time_limit_sec": 40 * 60,
        "specs": math_specs,
    })
    return booklets


def get_full_exam_spec(field: str, grade):
    """grade: 10 | 11 | 12 | 'all' (هر سه پایه — مشابه کنکور)."""
    if field == "ریاضی":
        booklets = _riazi_all() if grade == "all" else _riazi_single(int(grade))
    elif field == "تجربی":
        booklets = _tajrobi_all() if grade == "all" else _tajrobi_single(int(grade))
    else:
        raise ValueError(f"field نامعتبر برای آزمون کامل: {field}")

    for b in booklets:
        b["total"] = sum(s["count"] for s in b["specs"])
    return {
        "field": field,
        "grade": grade,
        "booklets": booklets,
        "total_questions": sum(b["total"] for b in booklets),
        "total_time_sec": sum(b["time_limit_sec"] for b in booklets),
    }
