# Multi-user sessions

Create and select a local session explicitly:

```text
taco session-new --user alice -w ./project
taco session-list --user alice -w ./project
taco chat --user alice --session <returned-uuid> -w ./project
taco run "Fix the parser" --user alice --session <returned-uuid> -w ./project
```

Python callers use `SessionSpace(project, trusted_user_id).create()` and then
`stream_session_events(task, session_workspace=project, user_id=trusted_user_id,
session_id=session_id)`. Both identity arguments are required together. Unknown
sessions fail rather than silently creating new history.

Each session has a private working copy under
`.tacoagent/sessions/<session_id>/workspace`. The session UUID determines the
workspace path; `owner.json` records the user and is checked on every open.
Listing sessions filters by ownership. Earlier user-hash directories remain
readable without moving active workspaces. Code, conversation,
memory, trace and checkpoints are scoped to that copy. Existing project files
are copied on creation; later sessions do not inherit another session's edits.
Symlinks/junctions, Git metadata, common dependency directories and TacoAgent
working notes are excluded. Dependencies may need installation in the copy.
Changes are not automatically merged into the source project. Inspect the
printed workspace path to retrieve the result.

An OS file lock covers the full session event stream (including memory writes).
A second turn on the same workspace fails with `SessionBusyError`; separate
workspaces can execute concurrently. Closing or failing a stream releases its
lease, and process termination releases the OS lock. Consumers must close
abandoned generators. Creation holds the source workspace lease while copying.
Legacy calls without identity still use the original workspace/session but now
also acquire a workspace execution lease. Existing history is preserved there.

This is application-level separation, not hostile-tenant security. `--user` is
a local selector, not authentication. A web service must derive identity from
authentication, authorize the project, and never expose arbitrary workspace
paths. Shell commands currently run with host privileges; untrusted users need
separate containers/OS identities and restricted mounts/network/credentials.
Callers bypassing the session driver, background shell descendants and external
IDE writers do not participate in its lock. Source copies are not transactional
against an IDE save. Use a quiescent source when creating sessions. For live IDE
collaboration, integrate editor document versions; a hash check followed by
rename alone is not an atomic compare-and-swap against external writers.

The Mem0 library uses process-global environment configuration. For multi-user
service deployment run sessions in separate worker processes/containers; do not
treat in-process Mem0 metadata/configuration as a tenant security boundary.
