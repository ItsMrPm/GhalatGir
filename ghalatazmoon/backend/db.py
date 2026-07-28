import sqlite3
import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.environ.get("GHALATAZMOON_DB", os.path.join(BASE_DIR, "ghalatazmoon.db"))
SCHEMA_PATH = os.path.join(BASE_DIR, "schema.sql")


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


# Lightweight migrations for databases created before these columns existed.
# (schema.sql uses CREATE TABLE IF NOT EXISTS, so existing DBs don't pick up
# new columns automatically — SQLite also has no ALTER COLUMN / IF NOT EXISTS.)
MIGRATIONS = [
    ("lessons", "category", "VARCHAR(30)"),
    ("mistakes", "base", "SMALLINT"),
    ("mistakes", "chapter_name", "TEXT"),
    ("mistakes", "topic_name", "TEXT"),
    ("mistakes", "answer_image_url", "TEXT"),
    ("mistakes", "understanding",
     "VARCHAR(20) CHECK (understanding IN ('understood','partial','not_understood'))"),
    ("exams", "mode", "VARCHAR(20) DEFAULT 'smart'"),
    ("exams", "progress", "TEXT"),
    ("exam_questions", "booklet_index", "SMALLINT DEFAULT 0"),
    ("exam_questions", "correct_answer", "VARCHAR(10)"),
    ("exam_questions", "lesson_name", "TEXT"),
    ("exam_questions", "base", "SMALLINT"),
    ("exam_questions", "topic_name", "TEXT"),
    ("exam_questions", "image_url", "TEXT"),
    ("exam_questions", "text", "TEXT"),
    ("exam_questions", "source_label", "VARCHAR(200)"),
    ("exam_questions", "qnum", "VARCHAR(20)"),
]


def run_migrations(conn):
    for table, column, coltype in MIGRATIONS:
        cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
        if column not in cols:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {coltype}")
    conn.commit()


def init_db(reset=False):
    if reset and os.path.exists(DB_PATH):
        os.remove(DB_PATH)
    conn = get_db()
    with open(SCHEMA_PATH, "r", encoding="utf-8") as f:
        conn.executescript(f.read())
    conn.commit()
    run_migrations(conn)
    conn.close()


def row_to_dict(row):
    if row is None:
        return None
    return dict(row)


def rows_to_list(rows):
    return [dict(r) for r in rows]
