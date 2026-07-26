"""Priority score calculation — ported from architecture.md section 5.

In the original design this runs nightly / event-driven inside Celery.
Here (no Celery available) it is simply called synchronously right after
a mistake is created or reviewed, which is fine at this data scale.
"""
from datetime import date, datetime

REASON_WEIGHT = {
    'conceptual': 20, 'forgotten_formula': 15, 'calculation': 8,
    'misread_question': 5, 'careless': 3, 'time_management': 5,
    'unknown': 0,
}


def calculate_priority(mistake: dict) -> float:
    score = 50.0

    # تکرار = مهم‌ترین سیگنال
    score += (mistake.get("repeat_count") or 0) * 15

    # هرچه سخت‌تر، اولویت بالاتر
    score += (mistake.get("difficulty") or 3) * 4

    # هرچه بیشتر از موعد مرور گذشته، فوری‌تر
    next_review_date = mistake.get("next_review_date")
    if next_review_date:
        nrd = next_review_date
        if isinstance(nrd, str):
            nrd = datetime.strptime(nrd, "%Y-%m-%d").date()
        days_overdue = (date.today() - nrd).days
        if days_overdue > 0:
            score += min(days_overdue * 2, 30)

    # علت مفهومی/فراموشی فرمول خطرناک‌تر از بی‌دقتی است
    score += REASON_WEIGHT.get(mistake.get("reason") or "unknown", 0)

    # هرچه correct_streak بالاتر، اولویت پایین‌تر (نزدیک mastered)
    score -= (mistake.get("correct_streak") or 0) * 10

    return max(0, min(100, round(score, 2)))
