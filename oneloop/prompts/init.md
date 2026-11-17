<!-- oneloop:stage=init -->
# Set up oneloop for this repository

oneloop takes GitHub issues to merged pull requests. Separate Claude sessions build, verify and review each change, and the rules that judge changes are read from this repository's base branch. You are drafting those rules. A maintainer reviews your drafts in a PR before any of them apply, so write what they would write, not boilerplate.

Read the repository first: README, manifests, scripts, CI config, test layout, how the app starts. Then write the files below. Keep each one short and specific to this repository. Do not touch any other file. If a file already exists, leave it as is and say so in `notes`.

## 1. `.oneloop.toml`

The commands that check a change, each one you have **run successfully** in this checkout. Leave a check empty rather than guess. Example:

```toml
[checks]
lint = "npm run lint"
typecheck = "npx tsc --noEmit"
test = "npm test --silent"
build = ""

[risk]
# Paths where a mistake is expensive: auth, billing, payments, data deletion, infra.
sensitive = ["src/auth/**"]
```

If the repo has no CI workflows, add `[ci]\nmode = "none"`. Only use keys shown here plus `[land] auto_merge = "low"|"medium"|"off"`.

## 2. The verify skill: write it to `{{skill_draft}}`

Claude Code does not let this session write under `.claude/`, so write the skill to `{{skill_draft}}`; oneloop moves it to `.claude/skills/verify/SKILL.md` afterwards. It is the skill a verifier session uses to run the real thing and check a change end to end, like a QA engineer. Frontmatter `name: verify` and a one-line `description`. Then exact, copy-pasteable steps:

- how to install and start the app or service (ports, env vars with fake values, seed data), and how to know it is ready
- how to drive it: CLI invocations, curl requests, or browser steps for a UI
- how to inspect results: output, logs, files, database state
- how to stop it and clean up

Make it fast and reliable. Test every step now, in this checkout, and fix what fails. Use only the tools already available here; do not install global software.

## 3. `REVIEW.md`

What a reviewer of this repository should pay attention to: the invariants that matter, the mistakes that are easy to make here, the conventions to hold changes to. Ten bullets at most, each concrete enough to apply to a diff.

## 4. `.oneloop/checklist.md`

Three to six reliability checks, each with a trigger readable from a diff, for the places in this codebase where a change most often breaks something. Format:

```
## RL-001: <title>
trigger: path <glob>, added /<regex>/
check: <what to confirm when the trigger fires>
origin: <why: a file, an incident, or "initial setup">
```

Report the check commands you verified and what you wrote, naming the verify skill by its final path.
