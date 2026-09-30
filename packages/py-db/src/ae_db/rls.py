"""Row-level security: the database itself enforces "a user sees only their own rows".

Two NOLOGIN roles; every connection SET LOCAL ROLE's into one of them per transaction (session.py):
  ae_app     user requests. On every user-owned table the policy is user_id = app_user_id(), where
             app_user_id() reads the transaction-local setting app.user_id. A query that forgets its
             WHERE user_id = ... still returns only the caller's rows, and an INSERT/UPDATE that would
             write another user's user_id is rejected.
  ae_system  trusted server paths (sign-in sync, payment webhooks, the trading engine, admin jobs): sees all.
Tables are FORCE'd, so even the owner is filtered when acting as ae_app. The login role (DATABASE_URL user)
must be a member of both roles; the migration grants that to the migrating user.
"""

from __future__ import annotations

APP_ROLE = "ae_app"
SYSTEM_ROLE = "ae_system"

# table -> the column that holds the owning user's id
USER_TABLES: dict[str, str] = {
    "users": "id",
    "subscriptions": "user_id",
    "payments": "user_id",
    "broker_accounts": "user_id",
    "broker_sessions": "user_id",
    "strategies": "user_id",
    "strategy_runs": "user_id",
    "trades": "user_id",
    "orders": "user_id",
    "trade_events": "user_id",
    "audit_log": "user_id",
}
# readable by users, written by the system only
PUBLIC_READ_TABLES = ("plans",)  # later tables: public_read_sql() in their migration (instruments: 0005)
# system only (no ae_app privileges at all)
SYSTEM_ONLY_TABLES = ("webhook_events",)


def table_sql(table: str, col: str = "user_id") -> list[str]:
    """Privileges + policies for one user-owned table (used by later migrations that add tables)."""
    return [
        f"GRANT SELECT, INSERT, UPDATE, DELETE ON {table} TO {APP_ROLE}, {SYSTEM_ROLE}",
        f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY",
        f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY",
        f"CREATE POLICY user_isolation ON {table} TO {APP_ROLE} USING ({col} = app_user_id()) "
        f"WITH CHECK ({col} = app_user_id())",
        f"CREATE POLICY system_all ON {table} TO {SYSTEM_ROLE} USING (true) WITH CHECK (true)",
    ]


def user_read_only_sql(table: str, col: str = "user_id") -> list[str]:
    """A user may read their own rows but never write them (admin-set data such as feature overrides)."""
    return [
        f"GRANT SELECT ON {table} TO {APP_ROLE}",
        f"GRANT SELECT, INSERT, UPDATE, DELETE ON {table} TO {SYSTEM_ROLE}",
        f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY",
        f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY",
        f"CREATE POLICY user_read ON {table} FOR SELECT TO {APP_ROLE} USING ({col} = app_user_id())",
        f"CREATE POLICY system_all ON {table} TO {SYSTEM_ROLE} USING (true) WITH CHECK (true)",
    ]


def system_only_sql(table: str) -> list[str]:
    """No user privileges at all (platform secrets, webhook payloads): only trusted server paths."""
    return [f"GRANT SELECT, INSERT, UPDATE, DELETE ON {table} TO {SYSTEM_ROLE}"]


def public_read_sql(table: str) -> list[str]:
    """Privileges for a platform table users may read but only the system writes (used by later migrations)."""
    return [
        f"GRANT SELECT ON {table} TO {APP_ROLE}",
        f"GRANT SELECT, INSERT, UPDATE, DELETE ON {table} TO {SYSTEM_ROLE}",
    ]


def upgrade_sql() -> list[str]:
    sql = [
        f"DO $$ BEGIN CREATE ROLE {APP_ROLE} NOLOGIN; EXCEPTION WHEN duplicate_object THEN NULL; END $$",
        f"DO $$ BEGIN CREATE ROLE {SYSTEM_ROLE} NOLOGIN; EXCEPTION WHEN duplicate_object THEN NULL; END $$",
        f"GRANT {APP_ROLE}, {SYSTEM_ROLE} TO CURRENT_USER",
        f"GRANT USAGE ON SCHEMA public TO {APP_ROLE}, {SYSTEM_ROLE}",
        """CREATE OR REPLACE FUNCTION app_user_id() RETURNS uuid LANGUAGE sql STABLE AS
           $$ SELECT nullif(current_setting('app.user_id', true), '')::uuid $$""",
        f"GRANT EXECUTE ON FUNCTION app_user_id() TO {APP_ROLE}, {SYSTEM_ROLE}",
    ]
    for t, col in USER_TABLES.items():
        sql += [
            f"GRANT SELECT, INSERT, UPDATE, DELETE ON {t} TO {APP_ROLE}, {SYSTEM_ROLE}",
            f"ALTER TABLE {t} ENABLE ROW LEVEL SECURITY",
            f"ALTER TABLE {t} FORCE ROW LEVEL SECURITY",
            f"CREATE POLICY user_isolation ON {t} TO {APP_ROLE} USING ({col} = app_user_id()) "
            f"WITH CHECK ({col} = app_user_id())",
            f"CREATE POLICY system_all ON {t} TO {SYSTEM_ROLE} USING (true) WITH CHECK (true)",
        ]
    for t in PUBLIC_READ_TABLES:
        sql += [f"GRANT SELECT ON {t} TO {APP_ROLE}", f"GRANT SELECT, INSERT, UPDATE, DELETE ON {t} TO {SYSTEM_ROLE}"]
    for t in SYSTEM_ONLY_TABLES:
        sql += [f"GRANT SELECT, INSERT, UPDATE, DELETE ON {t} TO {SYSTEM_ROLE}"]
    sql += [
        f"GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {APP_ROLE}, {SYSTEM_ROLE}",
        # users never delete their account row directly (deletion is a system workflow)
        f"REVOKE DELETE ON users FROM {APP_ROLE}",
        # audit log is append-only for everyone, owner included
        """CREATE OR REPLACE FUNCTION audit_log_append_only() RETURNS trigger LANGUAGE plpgsql AS
           $$ BEGIN RAISE EXCEPTION 'audit_log is append-only'; END $$""",
        """CREATE TRIGGER audit_log_no_change BEFORE UPDATE OR DELETE ON audit_log
           FOR EACH ROW EXECUTE FUNCTION audit_log_append_only()""",
    ]
    return sql


def downgrade_sql() -> list[str]:
    sql = [
        "DROP TRIGGER IF EXISTS audit_log_no_change ON audit_log",
        "DROP FUNCTION IF EXISTS audit_log_append_only()",
    ]
    for t in USER_TABLES:
        sql += [
            f"DROP POLICY IF EXISTS user_isolation ON {t}",
            f"DROP POLICY IF EXISTS system_all ON {t}",
            f"ALTER TABLE {t} NO FORCE ROW LEVEL SECURITY",
            f"ALTER TABLE {t} DISABLE ROW LEVEL SECURITY",
        ]
    sql += ["DROP FUNCTION IF EXISTS app_user_id()"]
    return sql
