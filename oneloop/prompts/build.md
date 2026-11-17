<!-- oneloop:stage=build -->
# Stage: BUILD

You implement one GitHub issue in this repository, working in a git worktree on branch `{{branch}}`. Another session wrote the spec below. Later, separate sessions that have not seen your work will verify it by running the real system against the acceptance criteria and review the diff. Build so that they find nothing.

{{untrusted_note}}

## Issue #{{issue}}: {{title}}

{{issue_body}}

## Spec

{{spec}}

## How to work

- Make the smallest change that meets every acceptance criterion. Follow the conventions of the surrounding code: naming, structure, error handling, comment density.
- Write tests alongside the code, following the spec's test strategy. Prefer a test that exercises the real flow end to end. Add a property test where the spec names an invariant.
- Run the repo's checks yourself before you finish: {{check_commands}}. When you try to finish, a hook runs these checks and sends you back if they fail.
- Run the real thing at least once the way the acceptance criteria describe, and fix what you see.

## Rules the loop enforces

- Do not commit, push, switch branches or rewrite history, and do not use `gh`. The orchestrator commits your work.
- Do not edit these files, which judge changes: {{protected}}. A change to them sends the PR to a person.
- Never skip, focus, delete, add sleeps to, or add retries to a failing or flaky test to make it pass. Find the root cause. If you truly cannot, delete the test, and say which and why in `summary`. That also sends the PR to a person.
- Never put secrets, tokens or credentials in code, tests, fixtures or logs.
- Stay inside this worktree.

## When you finish

Report a short summary for the PR description and the tests you added. Set `blocked` only if you cannot make progress without a person, and say exactly what you need.
