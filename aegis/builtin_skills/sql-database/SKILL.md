---
name: sql-database
description: Query and inspect SQL databases (sqlite/postgres/mysql): explore schema, write safe SELECT queries, and explain results. Use for any database task.
version: 1.0.0
metadata:
  category: data
  tags: [sql, database, sqlite, query]
---

## When to Use
Inspecting, querying, or explaining data in a SQL database (SQLite, PostgreSQL, MySQL). Use for schema exploration, ad-hoc SELECTs, and result interpretation — NOT for unattended schema/data mutations.

## Procedure
1. Identify the engine and connection: a `.db`/`.sqlite` file (SQLite), a `postgres://`/`mysql://` URL, or env vars (`DATABASE_URL`, `PGHOST`...). Ask if ambiguous.
2. Confirm the client binary is present (see Quick Reference). If missing, prefer `execute_code` with a driver lib (`sqlite3` stdlib, `psycopg2`, `pymysql`).
3. Explore schema FIRST — list tables, then describe the relevant ones. Never guess column names.
4. Draft a SELECT. Default to `LIMIT 50` (or a count) before pulling full result sets.
5. Run read-only. For non-SQLite, wrap exploration in a read-only transaction when possible.
6. Explain results in plain terms: what each column means, row count, notable nulls/dupes.

## Quick Reference
```bash
# SQLite
sqlite3 db.sqlite ".tables"                 # list tables
sqlite3 db.sqlite ".schema users"           # table DDL
sqlite3 -header -column db.sqlite "SELECT * FROM users LIMIT 50;"

# Postgres (psql)
psql "$DATABASE_URL" -c "\dt"               # list tables
psql "$DATABASE_URL" -c "\d users"          # describe table
psql "$DATABASE_URL" -c "SELECT * FROM users LIMIT 50;"

# MySQL
mysql -e "SHOW TABLES;" dbname
mysql -e "DESCRIBE users;" dbname
```
```python
# Portable read-only via execute_code (SQLite stdlib)
import sqlite3
con = sqlite3.connect("file:db.sqlite?mode=ro", uri=True)
print(con.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall())
```

## Pitfalls
- Mutations: never run INSERT/UPDATE/DELETE/DROP/ALTER without explicit user approval.
- SQL injection: parameterize (`?`/`%s`), never f-string user input into queries.
- Full scans: always LIMIT or aggregate before SELECT * on unknown-size tables.
- Engine dialect drift: `LIMIT` vs `TOP`, quoting (`"col"` vs `` `col` ``), `||` vs `CONCAT`.
- Secrets: don't print full connection strings/passwords into output.

## Verification
- `SELECT 1;` succeeds → connection works.
- Schema commands return the expected tables/columns before any query.
- Row counts are sane (`SELECT count(*)`); spot-check a few rows match the question asked.
