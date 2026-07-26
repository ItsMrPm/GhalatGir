from flask import Blueprint, request, jsonify
from db import get_db
from auth import login_required

bp = Blueprint("curriculum", __name__, url_prefix="/api/v1")


@bp.get("/lessons/")
@login_required
def list_lessons():
    field = request.args.get("field")
    grade = request.args.get("grade")
    q = "SELECT * FROM lessons WHERE 1=1"
    params = []
    if field:
        q += " AND field=?"
        params.append(field)
    if grade:
        q += " AND grade=?"
        params.append(grade)
    q += " ORDER BY order_index"
    conn = get_db()
    rows = conn.execute(q, params).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


@bp.get("/lessons/<int:lesson_id>/chapters/")
@login_required
def lesson_chapters(lesson_id):
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM chapters WHERE lesson_id=? ORDER BY order_index", (lesson_id,)
    ).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


@bp.get("/chapters/<int:chapter_id>/topics/")
@login_required
def chapter_topics(chapter_id):
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM topics WHERE chapter_id=? ORDER BY order_index", (chapter_id,)
    ).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])
