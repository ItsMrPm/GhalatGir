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
    category    VARCHAR(30),
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
    -- Free-form curriculum info entered by the user (the fixed per-lesson
    -- topic lists turned out to be wrong, so grade/chapter/topic are now
    -- typed by the student themself — a Konkur student practices questions
    -- from all three grades).
    base              SMALLINT,          -- grade the question belongs to: 10/11/12
    chapter_name      TEXT,
    topic_name        TEXT,
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
    answer_image_url  TEXT,              -- photo of the answer-sheet for this question
    student_note      TEXT,
    teacher_note      TEXT,
    tags              TEXT,              -- JSON array
    status            VARCHAR(20) DEFAULT 'active' CHECK (status IN (
                          'active','improving','mastered','archived')),
    -- Self-reported understanding after an educational review:
    --   understood | partial | not_understood
    understanding     VARCHAR(20) CHECK (understanding IN (
                          'understood','partial','not_understood')),
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
-- mode: smart            -> legacy "آزمون هوشمند" (single block, feedback at end)
--       educational      -> تست آموزشی: instant correction + answer sheet + understanding feedback
--       coverage         -> تست پوششی: N questions in a row, batch check at the end
--       custom_exam      -> آزمون شخصی‌سازی‌شده (single booklet, optional timer)
--       full_exam        -> آزمون کامل کنکوری (multiple booklets, each with its own timer)
CREATE TABLE IF NOT EXISTS exams (
    id              TEXT PRIMARY KEY,
    user_id         TEXT REFERENCES users(id) ON DELETE CASCADE,
    title           VARCHAR(200),
    source_type     VARCHAR(20) DEFAULT 'auto_generated',
    mode            VARCHAR(20) DEFAULT 'smart',
    config_snapshot TEXT,          -- JSON (includes booklet layout for full_exam)
    total_questions SMALLINT,
    time_limit_sec  INT,
    status          VARCHAR(20) DEFAULT 'pending',
    progress        TEXT,          -- JSON: current booklet index & booklet finish log
    started_at      TEXT,
    finished_at     TEXT,
    created_at      TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS exam_questions (
    id                TEXT PRIMARY KEY,
    exam_id           TEXT REFERENCES exams(id) ON DELETE CASCADE,
    mistake_id        TEXT REFERENCES mistakes(id) ON DELETE CASCADE,
    order_index       SMALLINT,
    booklet_index     SMALLINT DEFAULT 0,
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

-- =====================================================
-- Papers (past Konkur booklets: inside/outside/sample) + their questions
-- =====================================================
CREATE TABLE IF NOT EXISTS papers (
    id          TEXT PRIMARY KEY,
    user_id     TEXT REFERENCES users(id) ON DELETE CASCADE,
    title       VARCHAR(200) NOT NULL,
    field       VARCHAR(30) NOT NULL,
    year        SMALLINT,
    session     VARCHAR(20),
    booklets    TEXT,                 -- JSON: [{index,name,time_limit_sec}]
    created_at  TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS paper_questions (
    id            TEXT PRIMARY KEY,
    paper_id      TEXT REFERENCES papers(id) ON DELETE CASCADE,
    booklet_index SMALLINT DEFAULT 0,
    order_index   SMALLINT DEFAULT 0,
    lesson_name   TEXT,
    base          SMALLINT,
    options_count SMALLINT DEFAULT 4,
    correct_answer VARCHAR(10),
    text          TEXT,
    image_url     TEXT,
    topic_name    TEXT
);

CREATE INDEX IF NOT EXISTS idx_pq_paper ON paper_questions(paper_id, booklet_index, order_index);
-- =====================================================
-- Classes (teacher / student) 
-- =====================================================
CREATE TABLE IF NOT EXISTS classes (
  id          TEXT PRIMARY KEY,
  teacher_id  TEXT REFERENCES users(id) ON DELETE CASCADE,
  name        VARCHAR(200) NOT NULL,
  join_code   VARCHAR(12) UNIQUE NOT NULL,
  created_at  TEXT DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS class_members (
  class_id   TEXT REFERENCES classes(id) ON DELETE CASCADE,
  user_id    TEXT REFERENCES users(id) ON DELETE CASCADE,
  joined_at  TEXT DEFAULT (datetime('now')),
  PRIMARY KEY (class_id, user_id)
);
CREATE INDEX IF NOT EXISTS idx_classes_teacher ON classes(teacher_id);
CREATE INDEX IF NOT EXISTS idx_classes_code ON classes(join_code);
CREATE INDEX IF NOT EXISTS idx_cm_user ON class_members(user_id);
-- =====================================================
-- Test scheduler (cadence-based assignment)
-- =====================================================
CREATE TABLE IF NOT EXISTS test_programs (
  id TEXT PRIMARY KEY,
  user_id TEXT REFERENCES users(id) ON DELETE CASCADE,
  source TEXT NOT NULL, lesson_name TEXT, range_from INT NOT NULL, range_to INT NOT NULL,
  step INT NOT NULL DEFAULT 1, mode TEXT NOT NULL DEFAULT 'coverage', minutes INT, title TEXT,
  created_at TEXT DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS test_progress (
  id TEXT PRIMARY KEY, user_id TEXT REFERENCES users(id) ON DELETE CASCADE,
  program_id TEXT REFERENCES test_programs(id) ON DELETE CASCADE, test_number INT NOT NULL,
  status TEXT DEFAULT 'done', result TEXT, understanding TEXT, done_at TEXT,
  UNIQUE(program_id, test_number)
);
CREATE INDEX IF NOT EXISTS idx_tp_user ON test_programs(user_id);
CREATE INDEX IF NOT EXISTS idx_tprog_prog ON test_progress(program_id);



