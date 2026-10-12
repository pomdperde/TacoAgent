# LangGraph 1.2 integration

TacoAgent requires `langgraph>=1.2.11,<1.3`. The lower bound matches the
version verified by the workflow tests; the upper bound keeps this migration
within the 1.2 release series.

## Node failure handling

Both `build_workflow()` and `build_multi_agent_workflow()` configure the native
1.2 `set_node_defaults(error_handler=...)` API. A failed node supplies a
`NodeError` to the shared handler, which returns a `Command` routing to
`workflow_failure`. The terminal records the failed node, exception type and
message in `workflow_error`, clears `passed`, and creates a failure answer.
Normal successors do not run. The terminal has no error handler of its own.

Direct graph invocation returns that failed state. The public streaming
wrappers emit a `workflow_error` event, record a failed trace, then raise
`WorkflowExecutionError`. Session execution therefore uses its existing
exception path to persist a failed turn and release the workspace lease.
Consumers must exhaust or close event generators to release their resources.

No graph-level automatic retries are enabled: replaying an actor, supervisor,
or verifier can repeat filesystem changes or shell commands. Verification
feedback and its existing bounded actor loop are unchanged. Failure handling
does not roll back files or other external side effects.

## Streaming compatibility

The graph streams explicitly opt into `version="v2"` and read the structured
`type` and `data` fields instead of positional tuples. This protocol was
introduced in 1.1 and is supported by 1.2. Existing CLI events remain stable;
`workflow_error` is the new failure event. The beta v3 protocol is not enabled.

## Features not enabled

- Native node timeouts require async nodes. The current nodes perform blocking
  model calls and tool operations. Wrapping them in `asyncio.to_thread` would
  not stop the worker from writing files after coroutine cancellation.
  Existing request, command and workflow budgets remain in effect.
- `RunControl` drain/resume and `DeltaChannel` are not enabled. The graphs do
  not yet have a durable checkpointer, and state includes a live `RuntimeState`
  object. Durable recovery requires a separate serialization/context migration
  and a strategy for reconciling checkpoint state with workspace side effects.

References:
- https://docs.langchain.com/oss/python/langgraph/fault-tolerance
- https://docs.langchain.com/oss/python/releases/changelog

Regression tests cover every regular node's failure in both graphs, stopping
successors without retry, failure event propagation, and existing success,
verification retry, session and workspace-lock behavior.
