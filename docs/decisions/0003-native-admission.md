# ADR 0003: Separate native admission from observation

Status: Development implementation; not registered-execution authority.

## Decision

Keep OpenCode's stock `task` tool. Add a separately pinned enforcement
integration using only `tool.execute.before` and `tool.execute.after`. These
hooks reserve and settle capacity through a server-owned service. They do not
modify arguments, outputs, prompts, models, or tool definitions. The existing
out-of-process event observer remains observational and loads no instrumentation
plugins.

The pinned OpenCode version exposes these hooks and returns the child session
identity in task metadata. The implementation follows the pinned
[plugin interface](https://raw.githubusercontent.com/anomalyco/opencode/v1.18.19/packages/plugin/src/index.ts)
and [stock task implementation](https://raw.githubusercontent.com/anomalyco/opencode/v1.18.19/packages/opencode/src/tool/task.ts).

## Capacity policy

- The primary consumes one identity from the organization limit `N`.
- New task dispatch reserves a durable lifetime child slot before execution.
- Completed children retain slots because their sessions remain resumable.
- Resuming a known child uses its existing slot and permits one active call.
- A repeated dispatch ID is not permission to execute again.
- A failed or interrupted dispatch holds its slot. No timeout frees capacity
  based on an assumption that child creation failed.
- Closure compares admitted children with the persisted runtime tree and
  rejects unresolved calls or mismatched identities.

This conservative policy bounds both durable identities and simultaneous child
calls. It does not implement automatic child retirement or reclaim slots for
deleted children.

## Qualification boundary

The SQLite ledger and real-runtime development integration are implemented.
The hook uses a loopback development gateway; it is not yet connected to the
registered OCI Unix-socket transport. Production qualification must establish
that all allowed creation paths pass through admission, the hook and broker
cannot be bypassed, and restart reconciliation preserves the limit.

Registered native execution remains rejected. A development snapshot records
the admission profile and ledger reconciliation without claiming registered
interception qualification. Background-task promotion is not admitted by this
profile; incomplete terminal evidence invalidates closure.
