<!-- oneloop:stage=validate -->
# Stage: VALIDATE

A reviewer reported the findings below on a change that is checked out in the current directory. Your job is to keep only the real ones. For each finding, try to confirm it: read the code, trace the failure scenario, and where practical run something that shows it (a small script in `{{scratch_dir}}`, or an existing test). Do not modify files in the current directory.

- **confirmed**: you traced or reproduced the failure scenario.
- **rejected**: the scenario cannot happen, is already handled, or the finding misreads the code. Say why.
- **uncertain**: you could neither confirm nor rule it out.

Also correct the severity: important (bug, data loss, security, broken flow, missed criterion), nit, or pre_existing (real but not introduced by this diff, which is `{{base}}...HEAD`).

## Findings

{{findings}}

## Acceptance criteria, for context

{{criteria}}
