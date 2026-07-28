import json, uuid
from datetime import datetime
from flask import Blueprint, request, jsonify, g
from db import get_db
from auth import login_required
from routes.exams import _exam_public, _questions_with_mistakes, _booklet_layout

bp = Blueprint("papers", __name__, url_prefix="/api/v1/papers")


def _paper(conn, pid, uid):
    r = conn.execute("SELECT * FROM papers WHERE id=? AND user_id=?", (pid, uid)).fetchone()
    return dict(r) if r else None


@bp.get("/")
@login_required
def list_papers():
    conn = get_db()
    rows = conn.execute(
        "SELECT p.*, (SELECT COUNT(*) FROM paper_questions q WHERE q.paper_id=p.id) AS qcount "
        "FROM papers p WHERE p.user_id=? ORDER BY p.created_at DESC", (g.user_id,)
    ).fetchall()
    conn.close()
    out = [dict(r) for r in rows]
    for o in out:
        o["booklets"] = json.loads(o.get("booklets") or "[]")
    return jsonify(out)


@bp.post("/")
@login_required
def create_paper():
    d = request.get_json(force=True, silent=True) or {}
    title = (d.get("title") or "").strip()
    field = d.get("field")
    if not title or field not in ("ریاضی", "تجربی"):
        return jsonify({"detail": "عنوان و رشته (ریاضی/تجربی) لازم است."}), 400
    raw = d.get("booklets") or [{"name": "دفترچه ۱", "time_limit_sec": 0}]
    norm = [{"index": i, "name": (b.get("name") or f"دفترچه {i+1}"),
             "time_limit_sec": int(b.get("time_limit_sec") or 0)} for i, b in enumerate(raw)]
    yr = d.get("year")
    try:
        yr = int(yr) if yr not in (None, "") else None
    except Exception:
        yr = None
    pid = uuid.uuid4().hex
    conn = get_db()
    conn.execute(
        "INSERT INTO papers (id,user_id,title,field,year,session,booklets,created_at) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (pid, g.user_id, title, field, yr, d.get("session") or "نمونه",
         json.dumps(norm, ensure_ascii=False), datetime.utcnow().isoformat()))
    conn.commit(); conn.close()
    return jsonify({"id": pid, "booklets": norm}), 201


@bp.get("/<pid>/")
@login_required
def get_paper(pid):
    conn = get_db(); p = _paper(conn, pid, g.user_id)
    if not p:
        conn.close(); return jsonify({"detail": "پیدا نشد."}), 404
    p["booklets"] = json.loads(p.get("booklets") or "[]")
    p["questions"] = [dict(r) for r in conn.execute(
        "SELECT * FROM paper_questions WHERE paper_id=? ORDER BY booklet_index, order_index", (pid,)
    ).fetchall()]
    conn.close(); return jsonify(p)


@bp.delete("/<pid>/")
@login_required
def del_paper(pid):
    conn = get_db(); p = _paper(conn, pid, g.user_id)
    if not p:
        conn.close(); return jsonify({"detail": "پیدا نشد."}), 404
    conn.execute("DELETE FROM papers WHERE id=?", (pid,)); conn.commit(); conn.close()
    return "", 204


@bp.post("/<pid>/questions/")
@login_required
def add_questions(pid):
    conn = get_db(); p = _paper(conn, pid, g.user_id)
    if not p:
        conn.close(); return jsonify({"detail": "پیدا نشد."}), 404
    data = request.get_json(force=True, silent=True) or {}
    items = data.get("questions") if isinstance(data, dict) else data
    if not isinstance(items, list):
        conn.close(); return jsonify({"detail": "لیست questions لازم است."}), 400
    cur = {r["booklet_index"]: (r["m"] or 0) for r in conn.execute(
        "SELECT booklet_index, MAX(order_index) AS m FROM paper_questions WHERE paper_id=? "
        "GROUP BY booklet_index", (pid,)).fetchall()}
    added = 0
    for it in items:
        if not isinstance(it, dict):
            continue
        bi = int(it.get("booklet") if it.get("booklet") is not None else (it.get("booklet_index") or 0))
        nxt = cur.get(bi, 0)
        conn.execute(
            "INSERT INTO paper_questions (id,paper_id,booklet_index,order_index,lesson_name,base,"
            "options_count,correct_answer,text,image_url,topic_name) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (uuid.uuid4().hex, pid, bi, nxt,
             (it.get("lesson") or it.get("lesson_name") or "").strip() or None,
             it.get("base"), int(it.get("options_count") or 4),
             str(it.get("correct") or it.get("correct_answer") or ""),
             it.get("text") or None, it.get("image") or it.get("image_url") or None,
             it.get("topic") or it.get("topic_name") or None))
        cur[bi] = nxt + 1; added += 1
    conn.commit(); conn.close()
    return jsonify({"added": added})


@bp.delete("/<pid>/questions/")
@login_required
def clear_questions(pid):
    conn = get_db(); p = _paper(conn, pid, g.user_id)
    if not p:
        conn.close(); return jsonify({"detail": "پیدا نشد."}), 404
    n = conn.execute("DELETE FROM paper_questions WHERE paper_id=?", (pid,)).rowcount
    conn.commit(); conn.close()
    return jsonify({"deleted": n})


@bp.post("/<pid>/start/")
@login_required
def start_paper(pid):
    conn = get_db(); p = _paper(conn, pid, g.user_id)
    if not p:
        conn.close(); return jsonify({"detail": "پیدا نشد."}), 404
    pqs = conn.execute(
        "SELECT * FROM paper_questions WHERE paper_id=? ORDER BY booklet_index, order_index", (pid,)
    ).fetchall()
    if not pqs:
        conn.close(); return jsonify({"detail": "این آرشیو سؤالی ندارد."}), 400
    booklets = json.loads(p["booklets"] or "[]")
    planned = {b["index"]: sum(1 for pq in pqs if pq["booklet_index"] == b["index"]) for b in booklets}
    total_time = sum(b.get("time_limit_sec", 0) for b in booklets)
    config = {"mode": "full_exam", "source": "paper", "paper_id": p["id"], "paper_title": p["title"],
              "booklets": [{"index": b["index"], "name": b["name"],
                            "time_limit_sec": b.get("time_limit_sec", 0),
                            "planned": planned.get(b["index"], 0)} for b in booklets]}
    exam_id = uuid.uuid4().hex
    conn.execute(
        "INSERT INTO exams (id,user_id,title,source_type,mode,config_snapshot,total_questions,"
        "time_limit_sec,status,progress,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (exam_id, g.user_id, p["title"], "paper", "full_exam", json.dumps(config, ensure_ascii=False),
         len(pqs), total_time, "pending", json.dumps({"current_booklet": 0, "finished_booklets": []}),
         datetime.utcnow().isoformat()))
    for i, pq in enumerate(pqs):
        conn.execute(
            "INSERT INTO exam_questions (id,exam_id,mistake_id,order_index,booklet_index,slot_category,"
            "correct_answer,lesson_name,base,topic_name,image_url,text,source_label,qnum) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (uuid.uuid4().hex, exam_id, None, i, pq["booklet_index"], "paper", pq["correct_answer"],
             pq["lesson_name"], pq["base"], pq["topic_name"], pq["image_url"], pq["text"],
             p["title"], str((pq["order_index"] or 0) + 1)))
    conn.commit()
    exam = _exam_public(conn.execute("SELECT * FROM exams WHERE id=?", (exam_id,)).fetchone())
    qs = _questions_with_mistakes(conn, exam_id)
    exam["booklets"] = _booklet_layout(exam, qs)
    conn.close()
    return jsonify(exam), 201
