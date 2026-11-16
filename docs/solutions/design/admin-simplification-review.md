# Admin simplification review guard

Blind review was useful for finding visible clutter, but it did not reliably
turn all user symptoms into regression criteria. This guard keeps four exact
admin families executable without pretending a DOM check understands every
workflow.

`scripts/eval_admin_simplification.py` parses rendered HTML fixtures and checks
only annotated admin semantics: a binary editable setting has a checkbox; an
unavailable optional property renders nothing; repeated row mutation exposes a
selection/group or Shift-range path; and editable empty content exposes a
creation action/form. It deliberately permits semantic selects with three or
more choices and marks filter no-matches, failures, safety/clinical/legal
messages, and read-only sections out of scope.

The clean fixture runs from the registered advisory Claude `PostToolUse`
hook. The hook does not block writes and emits the required rendered-surface
review reminder. It explicitly says fixture success does **not** evaluate the
changed product source. For an affected admin screen, capture a rendered HTML
snapshot, annotate the owning semantic wrapper with the applicable
`data-admin-*` contract, then run the evaluator against that snapshot. An
unannotated snapshot fails as `coverage unavailable`; a hidden create/group
control does not count. Codex does not execute Claude lifecycle hooks, so the
CLI and unit test are the runtime-agnostic enforcement path. Negative fixtures
prove each failure is caught; clean counterexamples prove the exclusions pass.

This is not a generic UI lint platform. To cover a new screen, add a rendered
fixture only when one of these four exact contracts applies; otherwise use the
normal product interaction test and human review.
