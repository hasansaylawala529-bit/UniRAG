-- UniRAG: read-only role used by the application at question time.
-- Even if the LLM produced a harmful statement that slipped past sqlglot,
-- this role cannot write, and long queries are cut off.
-- Local development password; change it for any shared deployment.
CREATE ROLE unirag_reader LOGIN PASSWORD 'unirag_reader_pw';

GRANT CONNECT ON DATABASE unirag TO unirag_reader;
GRANT USAGE ON SCHEMA public TO unirag_reader;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO unirag_reader;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO unirag_reader;

ALTER ROLE unirag_reader SET default_transaction_read_only = on;
ALTER ROLE unirag_reader SET statement_timeout = '5s';
