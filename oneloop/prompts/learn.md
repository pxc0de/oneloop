<!-- oneloop:stage=learn -->
# Turn misses into rules

Below is what went wrong on oneloop's recent changes to {{repo}}: findings that blocked review, notes people wrote when sending changes back, reasons runs needed a person, and reverts. Propose reliability checklist rules that would have caught these earlier, and rules to prune.

A rule earns a place only if:

- the same kind of problem appears in **at least two** different PRs or issues below (cite them in `origin`), and
- its trigger can be read from a diff: `path <glob>` for changed files or `added /regex/` for added lines, and
- its check is something a reviewer can confirm by reading the change.

Before proposing a rule, try to disprove it: would it fire constantly on harmless changes? Would it have missed one of the cited cases? Drop it if so. Propose nothing rather than a weak rule.

Propose pruning an existing item when it fired many times and never led to a finding, or when its trigger no longer matches any file.

## Current checklist (base branch)

{{checklist}}

## Checklist item activity

{{activity}}

## Signals

{{signals}}
