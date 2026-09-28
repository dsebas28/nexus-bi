-- =============================================================================
-- NEXUS BI: read-only database role for AI-generated SQL
-- -----------------------------------------------------------------------------
-- Defence in depth for the AI analyst. Every model-written query is already
-- validated (ai_analyst/validation.py) and runs in a READ ONLY transaction with
-- a timeout. This role adds the last layer: even if both checks failed, the
-- connection has no privilege to change anything.
--
-- Run ONCE as a superuser (creating roles needs that privilege), passing the
-- password stored in AI_DB_PASSWORD in .env:
--
--   psql -U postgres -d nexus_bi -v ai_password="..." -f sql/roles.sql
--
-- Idempotent: re-running updates the password and grants.
-- =============================================================================

SELECT 'CREATE ROLE nexus_ai LOGIN' WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'nexus_ai')\gexec

ALTER ROLE nexus_ai WITH LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT CONNECTION LIMIT 5
    PASSWORD :'ai_password';

-- Session defaults enforced by the server, whatever the client asks for.
ALTER ROLE nexus_ai SET default_transaction_read_only = on;
ALTER ROLE nexus_ai SET statement_timeout = '10s';
ALTER ROLE nexus_ai SET idle_in_transaction_session_timeout = '30s';

GRANT CONNECT ON DATABASE nexus_bi TO nexus_ai;
REVOKE CREATE ON SCHEMA public FROM nexus_ai;

-- Read access to the analytics layer, ML outputs and a few reference tables only.
GRANT USAGE ON SCHEMA analytics, ml, core TO nexus_ai;
GRANT SELECT ON ALL TABLES IN SCHEMA analytics TO nexus_ai;          -- views and materialized views
GRANT SELECT ON ALL TABLES IN SCHEMA ml TO nexus_ai;
-- Views run with their owner's privileges, so base tables need no grant; core.locations is
-- needed because analytics.kpi_summary is SECURITY INVOKER and reads it directly.
GRANT SELECT ON core.states, core.categories, core.calendar, core.order_reviews, core.locations TO nexus_ai;
GRANT EXECUTE ON FUNCTION analytics.kpi_summary(date, date, text, smallint),
                          analytics.previous_period(date, date) TO nexus_ai;

-- Views are dropped and recreated by sql/views.sql: grant on future objects too.
ALTER DEFAULT PRIVILEGES FOR ROLE nexus_app IN SCHEMA analytics GRANT SELECT ON TABLES TO nexus_ai;
ALTER DEFAULT PRIVILEGES FOR ROLE nexus_app IN SCHEMA ml GRANT SELECT ON TABLES TO nexus_ai;
