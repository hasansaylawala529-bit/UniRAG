-- UniRAG: relational (structured) + vector (unstructured) schema
-- Live values (fees, enrollments, deadlines, amounts) live ONLY in relational tables.
-- Grant eligibility rules live ONLY in policy documents (doc_chunks), linked by grant_code.

-- ============================================================
-- Relational tables
-- ============================================================
CREATE TABLE departments (
    dept_id      SMALLINT PRIMARY KEY,
    code         VARCHAR(8)  NOT NULL UNIQUE,          -- CS, IT, ENTC
    name         VARCHAR(80) NOT NULL,
    hod_name     VARCHAR(80) NOT NULL
);

CREATE TABLE students (
    student_id      VARCHAR(12) PRIMARY KEY,           -- e.g. S2024CS007
    full_name       VARCHAR(80) NOT NULL,
    dept_id         SMALLINT NOT NULL REFERENCES departments(dept_id),
    year_of_study   SMALLINT NOT NULL CHECK (year_of_study BETWEEN 1 AND 4),
    cgpa            NUMERIC(4,2) CHECK (cgpa BETWEEN 0 AND 10),   -- 10-point scale; NULL for first-years
    gpa_4           NUMERIC(3,2) CHECK (gpa_4 BETWEEN 0 AND 4),   -- 4-point equivalent = cgpa * 0.4
    admission_year  SMALLINT NOT NULL,
    is_hosteller    BOOLEAN  NOT NULL,
    email           VARCHAR(120) NOT NULL UNIQUE
);

CREATE TABLE courses (
    course_id    INTEGER PRIMARY KEY,
    course_code  VARCHAR(10) NOT NULL UNIQUE,          -- e.g. CS301, CS3E1, OE301
    title        VARCHAR(100) NOT NULL,
    dept_id      SMALLINT NOT NULL REFERENCES departments(dept_id),
    semester     SMALLINT NOT NULL CHECK (semester BETWEEN 1 AND 8),
    credits      SMALLINT NOT NULL,
    course_type  VARCHAR(16) NOT NULL,                 -- core, lab, dept_elective, open_elective
    capacity     SMALLINT NOT NULL,
    instructor   VARCHAR(80) NOT NULL
);

CREATE TABLE enrollments (
    enrollment_id   INTEGER PRIMARY KEY,
    student_id      VARCHAR(12) NOT NULL REFERENCES students(student_id),
    course_id       INTEGER NOT NULL REFERENCES courses(course_id),
    term            VARCHAR(10) NOT NULL,              -- e.g. 2026-ODD
    status          VARCHAR(12) NOT NULL,              -- enrolled, dropped
    attendance_pct  NUMERIC(5,1),                      -- attendance so far this term
    grade           VARCHAR(2),                        -- NULL until results are declared
    UNIQUE (student_id, course_id, term)
);

CREATE TABLE timetable (
    slot_id      INTEGER PRIMARY KEY,
    course_id    INTEGER NOT NULL REFERENCES courses(course_id),
    day_of_week  VARCHAR(3) NOT NULL,                  -- Mon..Fri
    start_time   TIME NOT NULL,
    end_time     TIME NOT NULL,
    room         VARCHAR(16) NOT NULL,
    term         VARCHAR(10) NOT NULL
);

CREATE TABLE fee_structure (
    dept_id        SMALLINT NOT NULL REFERENCES departments(dept_id),
    year_of_study  SMALLINT NOT NULL,
    component      VARCHAR(16) NOT NULL,               -- tuition (annual), exam (per term), lab (annual), hostel (annual)
    amount         NUMERIC(10,2) NOT NULL,             -- INR
    academic_year  VARCHAR(7) NOT NULL,                -- e.g. 2026-27
    PRIMARY KEY (dept_id, year_of_study, component, academic_year)
);

CREATE TABLE fee_ledger (
    txn_id         INTEGER PRIMARY KEY,
    student_id     VARCHAR(12) NOT NULL REFERENCES students(student_id),
    academic_year  VARCHAR(7) NOT NULL,
    component      VARCHAR(16) NOT NULL,               -- tuition_inst1, tuition_inst2, exam, lab, hostel
    amount_due     NUMERIC(10,2) NOT NULL,
    amount_paid    NUMERIC(10,2) NOT NULL DEFAULT 0,
    due_date       DATE NOT NULL,
    paid_on        DATE,                               -- date of the latest payment
    status         VARCHAR(10) NOT NULL                -- paid, partial, pending, overdue
);

CREATE TABLE grants (
    grant_id              INTEGER PRIMARY KEY,
    grant_code            VARCHAR(12) NOT NULL UNIQUE, -- cross-source key, also tagged on policy chunks
    title                 VARCHAR(120) NOT NULL,
    sponsor               VARCHAR(120) NOT NULL,
    amount                NUMERIC(10,2) NOT NULL,      -- INR per award
    max_awards            SMALLINT NOT NULL,
    application_open      DATE NOT NULL,
    application_deadline  DATE NOT NULL,
    status                VARCHAR(10) NOT NULL,        -- open, upcoming, closed
    policy_doc            VARCHAR(80) NOT NULL         -- document that holds eligibility rules
);

CREATE TABLE grant_applications (
    application_id  INTEGER PRIMARY KEY,
    grant_id        INTEGER NOT NULL REFERENCES grants(grant_id),
    student_id      VARCHAR(12) NOT NULL REFERENCES students(student_id),
    submitted_on    DATE NOT NULL,
    status          VARCHAR(14) NOT NULL,              -- submitted, under_review, awarded, rejected
    UNIQUE (grant_id, student_id)
);

CREATE INDEX idx_students_dept_year ON students (dept_id, year_of_study);
CREATE INDEX idx_enrollments_student ON enrollments (student_id);
CREATE INDEX idx_ledger_student ON fee_ledger (student_id);
CREATE INDEX idx_ledger_status ON fee_ledger (status);

-- ============================================================
-- Vector tables (pgvector, 768-dim Gemini embeddings)
-- ============================================================
CREATE TABLE doc_chunks (
    chunk_id            SERIAL PRIMARY KEY,
    doc_id              VARCHAR(40) NOT NULL,
    doc_title           VARCHAR(120) NOT NULL,
    section             VARCHAR(160) NOT NULL,
    content             TEXT NOT NULL,
    embedding           vector(768),
    doc_type            VARCHAR(24) NOT NULL,          -- handbook, grant_policy, fee_policy, ...
    department          VARCHAR(8),                    -- NULL = applies to all
    year_applicability  SMALLINT[],                    -- NULL = all years
    effective_date      DATE,
    grant_codes         TEXT[],                        -- grant codes mentioned in this chunk
    metadata            JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE TABLE schema_cards (
    table_name  VARCHAR(40) PRIMARY KEY,
    card_text   TEXT NOT NULL,
    embedding   vector(768)
);

CREATE TABLE entity_texts (
    entity_type  VARCHAR(16) NOT NULL,                 -- grant, course
    entity_key   VARCHAR(16) NOT NULL,                 -- grant_code / course_code
    text         TEXT NOT NULL,
    embedding    vector(768),
    PRIMARY KEY (entity_type, entity_key)
);

CREATE TABLE router_exemplars (
    exemplar_id  SERIAL PRIMARY KEY,
    question     TEXT NOT NULL,
    route        VARCHAR(16) NOT NULL,                 -- sql, docs, hybrid, out_of_scope
    embedding    vector(768)
);

CREATE INDEX idx_chunks_emb    ON doc_chunks       USING hnsw (embedding vector_cosine_ops);
CREATE INDEX idx_cards_emb     ON schema_cards     USING hnsw (embedding vector_cosine_ops);
CREATE INDEX idx_entities_emb  ON entity_texts     USING hnsw (embedding vector_cosine_ops);
CREATE INDEX idx_exemplars_emb ON router_exemplars USING hnsw (embedding vector_cosine_ops);
CREATE INDEX idx_chunks_grants ON doc_chunks USING gin (grant_codes);
CREATE INDEX idx_chunks_meta   ON doc_chunks USING gin (metadata);
CREATE INDEX idx_chunks_trgm   ON doc_chunks USING gin (content gin_trgm_ops);
