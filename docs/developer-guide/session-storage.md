# Session Storage

Session storage is the crash-recovery backbone. AEGIS records enough session, run, and trace metadata for a later process to locate work, inspect recent context, and resume safely.

## Stored concepts

- Session id, title, source, profile, lineage, and timestamps.
- User/assistant/tool messages with ordering and role hygiene.
- Run ids, trace ids, command/tool metadata, and cost or token snapshots.
- Compression summaries, resume pointers, and export metadata.
- Search indexes for retrieval and recovery.

## Contract

Adding a session feature requires tests for write, read, search/export where relevant, and recovery after process restart. Deletion or pruning must preserve safety around active sessions and user-requested exports.
