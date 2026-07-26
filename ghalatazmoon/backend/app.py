import os
from flask import Flask, jsonify, send_from_directory

from db import init_db, get_db, DB_PATH
from seed_data import seed_curriculum

from routes.auth_routes import bp as auth_bp
from routes.curriculum import bp as curriculum_bp
from routes.mistakes import bp as mistakes_bp
from routes.exams import bp as exams_bp
from routes.analytics import bp as analytics_bp

FRONTEND_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "frontend")

app = Flask(__name__, static_folder=None)

app.register_blueprint(auth_bp)
app.register_blueprint(curriculum_bp)
app.register_blueprint(mistakes_bp)
app.register_blueprint(exams_bp)
app.register_blueprint(analytics_bp)


@app.after_request
def add_cors_headers(resp):
    # Same-origin in production (frontend served by this app), but permissive
    # CORS is kept here too so the SPA can also be opened from a different
    # dev port (e.g. `npm run dev` / VS Code Live Server) without changes.
    resp.headers["Access-Control-Allow-Origin"] = "*"
    resp.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization"
    resp.headers["Access-Control-Allow-Methods"] = "GET, POST, PATCH, DELETE, OPTIONS"
    return resp


@app.route("/api/v1/<path:_any>", methods=["OPTIONS"])
def cors_preflight(_any):
    return "", 204


@app.get("/api/v1/health/")
def health():
    return jsonify({"status": "ok"})


@app.get("/")
def index():
    return send_from_directory(FRONTEND_DIR, "index.html")


@app.get("/<path:filename>")
def frontend_files(filename):
    return send_from_directory(FRONTEND_DIR, filename)


if __name__ == "__main__":
    fresh = not os.path.exists(DB_PATH)
    init_db(reset=False)
    if fresh:
        conn = get_db()
        seed_curriculum(conn)
        conn.close()
        print(f"[ghalatazmoon] new database created at {DB_PATH}, curriculum seeded.")
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5001)), debug=True)
