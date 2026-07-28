import uuid
from datetime import datetime
from flask import Blueprint, request, jsonify, g
from db import get_db
from auth import login_required

bp = Blueprint("scheduler", __name__, url_prefix="/api/v1/scheduler")
MODES = {"exam", "coverage", "mixed"}


def _prog(conn, pid, uid):
    r = conn.execute("SELECT * FROM test_programs WHERE id=? AND user_id=?", (pid, uid)).fetchone()
    return dict(r) if r else None


def _stats(conn, pid):
    done = conn.execute("SELECT COUNT(*) c FROM test_progress WHERE program_id=?", (pid,)).fetchone()["c"]
    r = conn.execute("SELECT SUM(CASE WHEN result='correct' THEN 1 ELSE 0 END) c, "
                     "SUM(CASE WHEN result='wrong' THEN 1 ELSE 0 END) w FROM test_progress "
                     "WHERE program_id=? AND result IN ('correct','wrong')", (pid,)).fetchone()
    c = r["c"] or 0; w = r["w"] or 0
    return {"done": done, "correct": c, "wrong": w, "accuracy_pct": round(100 * c / (c + w), 2) if (c + w) else None}


@bp.post("/programs/")
@login_required
def create_program():
    d = request.get_json(force=True, silent=True) or {}
    src = (d.get("source") or "").strip()
    if not src:
        return jsonify({"detail": "منبع لازم است."}), 400
    try:
        rf = int(d.get("range_from")); rt = int(d.get("range_to")); step = int(d.get("step") or 1)
    except Exception:
        return jsonify({"detail": "محدوده/گام نامعتبر است."}), 400
    if rf < 1 or rt < rf or step < 1:
        return jsonify({"detail": "محدوده/گام نامعتبر است."}), 400
    mode = d.get("mode") or "coverage"
    if mode not in MODES:
        return jsonify({"detail": "حالت نامعتبر است."}), 400
    minutes = d.get("minutes")
    if mode == "exam" and not minutes:
        return jsonify({"detail": "برای حالت آزمونی دقیقه لازم است."}), 400
    minutes = int(minutes) if minutes else None
    pid = uuid.uuid4().hex
    conn = get_db()
    conn.execute("INSERT INTO test_programs (id,user_id,source,lesson_name,range_from,range_to,step,mode,minutes,title,created_at) "
                 "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                 (pid, g.user_id, src, (d.get("lesson_name") or "").strip() or None, rf, rt, step, mode, minutes,
                  (d.get("title") or "").strip() or None, datetime.utcnow().isoformat()))
    conn.commit(); conn.close()
    return jsonify({"id": pid}), 201


@bp.get("/programs/")
@login_required
def list_programs():
    conn = get_db()
    rows = conn.execute("SELECT * FROM test_programs WHERE user_id=? ORDER BY created_at DESC", (g.user_id,)).fetchall()
    out = []
    for r in rows:
        p = dict(r); p["progress"] = _stats(conn, p["id"]); p["total"] = p["range_to"] - p["range_from"] + 1; out.append(p)
    conn.close()
    return jsonify(out)


@bp.patch("/programs/<pid>/")
@login_required
def update_program(pid):
    conn = get_db(); p = _prog(conn, pid, g.user_id)
    if not p:
        conn.close(); return jsonify({"detail": "یافت نشد."}), 404
    d = request.get_json(force=True, silent=True) or {}
    fields = {k: d[k] for k in ("source", "lesson_name", "range_from", "range_to", "step", "mode", "minutes", "title") if k in d}
    if "mode" in fields and fields["mode"] not in MODES:
        conn.close(); return jsonify({"detail": "حالت نامعتبر."}), 400
    if fields.get("mode", p["mode"]) == "exam" and not (fields.get("minutes", p["minutes"])):
        conn.close(); return jsonify({"detail": "برای آزمونی دقیقه لازم است."}), 400
    if fields:
        conn.execute("UPDATE test_programs SET " + ", ".join(f"{k}=?" for k in fields) + " WHERE id=?", (*fields.values(), pid))
        conn.commit()
    conn.close(); return jsonify({"ok": True})


@bp.delete("/programs/<pid>/")
@login_required
def delete_program(pid):
    conn = get_db(); p = _prog(conn, pid, g.user_id)
    if not p:
        conn.close(); return jsonify({"detail": "یافت نشد."}), 404
    conn.execute("DELETE FROM test_programs WHERE id=?", (pid,)); conn.commit(); conn.close()
    return "", 204


@bp.post("/programs/<pid>/session/")
@login_required
def session_numbers(pid):
    conn = get_db(); p = _prog(conn, pid, g.user_id)
    if not p:
        conn.close(); return jsonify({"detail": "یافت نشد."}), 404
    d = request.get_json(force=True, silent=True) or {}
    count = d.get("count"); count = int(count) if count else None
    done = {r["test_number"] for r in conn.execute("SELECT test_number FROM test_progress WHERE program_id=?", (pid,)).fetchall()}
    conn.close()
    rf, rt, step = p["range_from"], p["range_to"], p["step"]
    candidates = [n for n in range(rf, rt + 1) if n not in done]
    phase = None; selected = []
    if step <= 1:
        selected = candidates
    else:
        for ph in range(step):
            if any((n - rf) % step == ph for n in candidates):
                phase = ph; break
        if phase is not None:
            selected = [n for n in candidates if (n - rf) % step == phase]
    if count:
        selected = selected[:count]
    if p["mode"] == "mixed":
        edu, cov = selected[0::2], selected[1::2]
    else:
        edu, cov = [], []
    not_done = [n for n in range(rf, rt + 1) if n not in done]
    return jsonify({"program": p, "selected": selected, "educational": edu, "coverage": cov, "not_done": not_done,
                    "minutes": p["minutes"], "phase": phase, "remaining": len(candidates) - len(selected),
                    "done_total": len(done), "range_total": rt - rf + 1})


@bp.post("/programs/<pid>/done/")
@login_required
def mark_done(pid):
    conn = get_db(); p = _prog(conn, pid, g.user_id)
    if not p:
        conn.close(); return jsonify({"detail": "یافت نشد."}), 404
    d = request.get_json(force=True, silent=True) or {}
    items = d.get("items") or []
    now = datetime.utcnow().isoformat(); saved = 0
    for it in items:
        n = it.get("number")
        if n is None:
            continue
        conn.execute("INSERT INTO test_progress (id,user_id,program_id,test_number,status,result,understanding,done_at) "
                     "VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(program_id,test_number) DO UPDATE SET "
                     "result=excluded.result, understanding=excluded.understanding, done_at=excluded.done_at, status='done'",
                     (uuid.uuid4().hex, g.user_id, pid, int(n), "done", it.get("result"), it.get("understanding"), now))
        saved += 1
    conn.commit(); conn.close()
    return jsonify({"saved": saved})


@bp.get("/programs/<pid>/progress/")
@login_required
def prog(pid):
    conn = get_db(); p = _prog(conn, pid, g.user_id)
    if not p:
        conn.close(); return jsonify({"detail": "یافت نشد."}), 404
    rows = conn.execute("SELECT test_number,result,understanding,done_at FROM test_progress WHERE program_id=? ORDER BY test_number", (pid,)).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])
