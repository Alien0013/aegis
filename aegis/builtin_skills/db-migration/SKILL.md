---
name: db-migration
description: Write a safe, reversible database schema migration — expand/contract, backfill without locking, and deploy with zero downtime. Use when changing a schema that has live data or live traffic.
version: 1.0.0
metadata:
  category: data
  tags: [database, migration, schema, sql, zero-downtime]
---

## When to Use
You need to change a schema (add/drop/rename a column or table, change a type, add a constraint or index) on a database that holds real data or serves live traffic. The risk is **data loss and downtime**, so migrations are forward-careful and reversible.

## Procedure
1. **Separate schema from data.** A migration changes structure; a backfill moves/fills data. Keep them in distinct, individually reversible steps — a failed backfill shouldn't strand the schema.
2. **Use expand → migrate → contract** for anything renaming or retyping, so old and new code both work during the rollout:
   - **Expand:** add the new column/table (nullable, no default-rewrite). Old code ignores it.
   - **Backfill:** populate it in **batches** (e.g. 1–10k rows per commit) so you never hold a long lock or a giant transaction. Make the backfill idempotent and resumable.
   - **Dual-write:** ship code that writes both old and new.
   - **Contract:** once new code reads the new column and the backfill is verified, drop the old one in a *later* deploy.
3. **Avoid blocking operations.** Adding a non-nullable column with a default, or an index without `CONCURRENTLY`, can lock the table. Add nullable + backfill + then set NOT NULL via a validated constraint; build indexes concurrently.
4. **Always write the down migration.** Every up has a tested down. If down is impossible (a destructive drop), say so explicitly and gate it behind a backup.
5. **Back up first** for anything destructive. Verify the backup restores before you run the drop.
6. **Test on a copy of production-shaped data** — row counts and distributions reveal lock duration and timeouts that an empty dev DB hides.

## Quick Reference
```sql
-- expand (safe): nullable, no table rewrite
ALTER TABLE users ADD COLUMN email_verified boolean;          -- nullable, instant
-- backfill in batches (resumable, no long lock)
UPDATE users SET email_verified = false
 WHERE email_verified IS NULL AND id BETWEEN :lo AND :hi;
-- index without locking writes (postgres)
CREATE INDEX CONCURRENTLY idx_users_email ON users(email);
-- validate constraint without a full-table lock
ALTER TABLE users ADD CONSTRAINT chk ... NOT VALID;  ALTER TABLE users VALIDATE CONSTRAINT chk;
```

## Pitfalls
- `ADD COLUMN ... NOT NULL DEFAULT x` on a big table → full rewrite + long lock (varies by engine/version).
- One giant `UPDATE` of millions of rows → lock contention, replication lag, timeout, bloat.
- Rename-in-place (`ALTER ... RENAME`) with a single deploy → old code breaks mid-rollout. Use expand/contract.
- No down migration, or an untested one that fails when you actually need it.
- Destructive change with no verified backup.
- Testing only on an empty dev DB, so the production lock surprises you.

## Verification
- Up **and** down both run cleanly on a production-shaped copy; row counts match expectations after each.
- Backfill is idempotent and resumable: running it twice is safe; interrupting and rerunning completes.
- No statement holds a lock long enough to affect live traffic (measured, not assumed).
- App works at every intermediate state (old code + new schema, and new code + old schema) — the rollout has no flag-day.
- A verified backup exists before any destructive step.
