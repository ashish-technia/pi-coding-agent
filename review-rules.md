# Code review rules

The review agent checks every diff against these rules in addition to the approved plan.
Edit this file to match your team's standards; it is read on each review.

## Must (reject if violated)

- The diff implements every step of the approved plan for the current phase, and nothing outside it.
- No secrets, tokens, or credentials are added to the code or config.
- No new runtime dependency is introduced unless the plan called for it.
- Existing public interfaces (exported functions, API routes, config keys) keep their contracts unless the plan changes them explicitly.
- Error paths are handled: no swallowed exceptions, no bare `except`, no `console.log` debugging left behind.

## Should (mention, do not reject alone)

- Tests exist or were updated for the changed behaviour.
- Names and structure follow the surrounding code's conventions.
- Comments explain *why*, not *what*, and stale comments are removed.
- Documentation touched by the change is updated.
