-- UniRAG: load the generated CSVs (data/seed is mounted at /seed).
-- Runs automatically the first time the database volume is created.
-- To reload after regenerating data:  docker compose down -v && docker compose up -d
COPY departments        FROM '/seed/departments.csv'        WITH (FORMAT csv, HEADER true);
COPY students           FROM '/seed/students.csv'           WITH (FORMAT csv, HEADER true);
COPY courses            FROM '/seed/courses.csv'            WITH (FORMAT csv, HEADER true);
COPY enrollments        FROM '/seed/enrollments.csv'        WITH (FORMAT csv, HEADER true);
COPY timetable          FROM '/seed/timetable.csv'          WITH (FORMAT csv, HEADER true);
COPY fee_structure      FROM '/seed/fee_structure.csv'      WITH (FORMAT csv, HEADER true);
COPY fee_ledger         FROM '/seed/fee_ledger.csv'         WITH (FORMAT csv, HEADER true);
COPY grants             FROM '/seed/grants.csv'             WITH (FORMAT csv, HEADER true);
COPY grant_applications FROM '/seed/grant_applications.csv' WITH (FORMAT csv, HEADER true);
