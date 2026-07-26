-- =====================================================
-- GhalatAzmoon — SQLite schema
-- Ported from architecture.md (PostgreSQL) with the same
-- tables, columns and relationships. Differences vs. Postgres:
--   UUID       -> TEXT (uuid4 hex, generated in Python)
--   ENUM       -> TEXT + CHECK constraint
--   ARRAY      -> TEXT (JSON-encoded list)
--   JSONB      -> TEXT (JSON-encoded)
--   TIMESTAMPTZ-> TEXT (ISO-8601, UTC)
--   gen_random_uuid() / now() -> supplied by the app layer
-- =====================================================

PRAGMA foreign_keys = ON;

-- =====================================================
-- Users
-- =====================================================
CREATE TABLE IF NOT EXISTS users (
    id                  TEXT PRIMARY KEY,
    phone_number        VARCHAR(15) UNIQUE NOT NULL,
    email               VARCHAR(255) UNIQUE,
    full_name           VARCHAR(150) NOT NULL,
    grade               SMALLINT,
    field               VARCHAR(30),
    target_konkur_year  SMALLINT,
    password_hash       VARCHAR(255) NOT NULL,
    avatar_url          TEXT,
    theme_preference    VARCHAR(10) DEFAULT 'system',
    streak_count        INT DEFAULT 0,
    longest_streak      INT DEFAULT 0,
    last_active_date    DATE,
    is_premium          BOOLEAN DEFAULT 0,
    created_at          TEXT DEFAULT (datetime('now')),
    updated_at          TEXT DEFAULT (datetime('now'))
);

-- =====================================================
-- Curriculum hierarchy: Lesson > Chapter > Topic
-- =====================================================
CREATE TABLE IF NOT EXISTS lessons (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        VARCHAR(100) NOT NULL,
    field       VARCHAR(30) NOT NULL,
    grade       SMALLINT NOT NULL,
    order_index SMALLINT DEFAULT 0
);

CREATE TABLE IF NOT EXISTS chapters (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    lesson_id   INTEGER REFERENCES lessons(id) ON DELETE CASCADE,
    name        VARCHAR(150) NOT NULL,
    order_index SMALLINT DEFAULT 0
);

CREATE TABLE IF NOT EXISTS topics (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    chapter_id  INTEGER REFERENCES chapters(id) ON DELETE CASCADE,
    name        VARCHAR(150) NOT NULL,
    order_index SMALLINT DEFAULT 0
);

-- =====================================================
-- Mistakes (core of the system)
-- =====================================================
-- reason:  conceptual | calculation | careless | forgotten_formula
--          | time_management | misread_question | unknown
-- status:  active | improving | mastered | archived
CREATE TABLE IF NOT EXISTS mistakes (
    id                TEXT PRIMARY KEY,
    user_id           TEXT REFERENCES users(id) ON DELETE CASCADE,
    lesson_id         INTEGER REFERENCES lessons(id),
    chapter_id        INTEGER REFERENCES chapters(id),
    topic_id          INTEGER REFERENCES topics(id),
    difficulty        SMALLINT CHECK (difficulty BETWEEN 1 AND 5),
    source_book       VARCHAR(200),
    question_number   VARCHAR(20),
    correct_answer    VARCHAR(10),
    student_answer    VARCHAR(10),
    reason            VARCHAR(30) DEFAULT 'unknown' CHECK (reason IN (
                          'conceptual','calculation','careless',
                          'forgotten_formula','time_management',
                          'misread_question','unknown')),
    reason_note       TEXT,
    image_url         TEXT,
    student_note      TEXT,
    teacher_note      TEXT,
    tags              TEXT,              -- JSON array
    status            VARCHAR(20) DEFAULT 'active' CHECK (status IN (
                          'active','improving','mastered','archived')),
    priority_score    NUMERIC(5,2) DEFAULT 50.0,
    repeat_count      SMALLINT DEFAULT 0,
    correct_streak    SMALLINT DEFAULT 0,
    next_review_date  DATE,
    interval_days     SMALLINT DEFAULT 1,
    first_seen_at     TEXT DEFAULT (datetime('now')),
    last_reviewed_at  TEXT,
    created_at        TEXT DEFAULT (datetime('now')),
    updated_at        TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_mistakes_user_status ON mistakes(user_id, status);
CREATE INDEX IF NOT EXISTS idx_mistakes_next_review ON mistakes(user_id, next_review_date);
CREATE INDEX IF NOT EXISTS idx_mistakes_lesson ON mistakes(user_id, lesson_id);
CREATE INDEX IF NOT EXISTS idx_mistakes_priority ON mistakes(user_id, priority_score DESC);

-- =====================================================
-- Exams
-- =====================================================
-- source_type: auto_generated | manual | daily_review
CREATE TABLE IF NOT EXISTS exams (
    id              TEXT PRIMARY KEY,
    user_id         TEXT REFERENCES users(id) ON DELETE CASCADE,
    title           VARCHAR(200),
    source_type     VARCHAR(20) DEFAULT 'auto_generated',
    config_snapshot TEXT,          -- JSON
    total_questions SMALLINT,
    time_limit_sec  INT,
    status          VARCHAR(20) DEFAULT 'pending',
    started_at      TEXT,
    finished_at     TEXT,
    created_at      TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS exam_questions (
    id                TEXT PRIMARY KEY,
    exam_id           TEXT REFERENCES exams(id) ON DELETE CASCADE,
    mistake_id        TEXT REFERENCES mistakes(id) ON DELETE CASCADE,
    order_index       SMALLINT,
    slot_category     VARCHAR(20),
    is_flagged        BOOLEAN DEFAULT 0,
    student_answer    VARCHAR(10),
    is_correct        BOOLEAN,
    time_spent_sec    INT,
    answered_at       TEXT
);

-- =====================================================
-- Review History
-- =====================================================
CREATE TABLE IF NOT EXISTS review_history (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    mistake_id        TEXT REFERENCES mistakes(id) ON DELETE CASCADE,
    exam_question_id  TEXT REFERENCES exam_questions(id),
    result            BOOLEAN,
    interval_before   SMALLINT,
    interval_after    SMALLINT,
    reviewed_at       TEXT DEFAULT (datetime('now'))
);

-- =====================================================
-- Study Sessions (streaks & time-on-task)
-- =====================================================
CREATE TABLE IF NOT EXISTS study_sessions (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id             TEXT REFERENCES users(id) ON DELETE CASCADE,
    session_date        DATE NOT NULL,
    mistakes_added      SMALLINT DEFAULT 0,
    questions_reviewed  SMALLINT DEFAULT 0,
    duration_sec        INT DEFAULT 0,
    UNIQUE(user_id, session_date)
);

-- =====================================================
-- Daily rollup for fast analytics
-- =====================================================
CREATE TABLE IF NOT EXISTS daily_stats (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id             TEXT REFERENCES users(id) ON DELETE CASCADE,
    stat_date           DATE NOT NULL,
    lesson_id           INTEGER REFERENCES lessons(id),
    mistakes_added      SMALLINT DEFAULT 0,
    mistakes_mastered   SMALLINT DEFAULT 0,
    accuracy_pct        NUMERIC(5,2),
    UNIQUE(user_id, stat_date, lesson_id)
);

-- =====================================================
-- Weakness patterns (section 7 of architecture.md)
-- =====================================================
CREATE TABLE IF NOT EXISTS weakness_patterns (
    id            TEXT PRIMARY KEY,
    user_id       TEXT REFERENCES users(id) ON DELETE CASCADE,
    topic_id      INTEGER REFERENCES topics(id),
    reason        VARCHAR(30),
    mistake_count SMALLINT,
    message       TEXT,
    created_at    TEXT DEFAULT (datetime('now')),
    dismissed     BOOLEAN DEFAULT 0
);
