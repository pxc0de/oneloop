# oneloop

oneloop takes a GitHub issue to a merged pull request. Each stage runs as its own headless Claude Code session, and the stages that judge the work never see how it was made. You are asked to act only when a run cannot safely continue without you.

```
issue ─► SPEC ─► BUILD ─► CHECKS ─► VERIFY ─► REVIEW (+validator) ─► SHIP ─► CI ─► LAND
                   ▲                  │            │                          │
                   └──── fix (resumes the builder's session, ≤3 rounds each) ─┘
```

| Stage | Session | What it does |
|---|---|---|
| SPEC | fresh, read-only | Turns the issue into acceptance criteria, a plan and a test strategy. If the issue is ambiguous, it posts questions on the issue and stops. |
| BUILD | fresh; fix rounds resume it | Implements and tests. A Stop hook runs the repo's checks before it may finish. It cannot commit, push or call `gh`. |
| CHECKS | no model | oneloop runs lint, typecheck, test and build itself. It does not take an agent's word that they pass. |
| VERIFY | fresh, read-only | Runs the real thing using the repo's verify skill and grades each criterion as pass, fail or couldn't-run, with evidence. |
| REVIEW | one fresh reviewer per round, plus a fresh validator | Reviews against REVIEW.md and the checklist items the diff triggers. The validator keeps only the findings it can confirm. Later rounds review only the new diff. |
| SHIP, CI | no model | Pushes, opens the PR and waits for CI. A red CI goes back to BUILD with the logs. |
| LAND | no model, except the brief | Re-reads everything live, then merges low-risk changes or hands you a two-minute brief. |

## What needs you

You get notified only for these, and each one appears first in the dashboard, the `/oneloop` pane and `oneloop status`:

- **Questions on the issue**: the spec found a decision only you can make. Answer on the issue; queue mode resumes the run by itself.
- **Approve to merge**: the change is medium or high risk, or auto-merge is off for the repo. The brief says what changed, the evidence, the risks and which lines to look at.
- **Couldn't run**: a check, the verify skill or CI could not run. oneloop never counts that as a pass.
- **Round cap reached**: checks, verify, review or CI is still failing after 3 fix rounds. The third review round tells the builder to rethink its approach first.
- **Budget reached**, **builder blocked**, **conflict**, **changed by someone**: someone commented, pushed, requested changes or added the `oneloop:hold` label.

## Install

Requires Python 3.11+, `git`, `gh` (logged in) and `claude` (2.1.287+). Python packages: none.

```sh
git clone git@github.com:pxc0de/oneloop.git
ln -s "$PWD/oneloop/bin/oneloop" ~/.local/bin/oneloop   # or: pip install -e oneloop
oneloop doctor
```

## Use

```sh
cd your-repo
oneloop init                 # once per repo: drafts the verify skill, REVIEW.md, checklist and config as a PR
oneloop run 123              # the whole loop for one issue
oneloop run --label -j 3     # every issue labeled `oneloop`, 3 at a time
oneloop run --label --watch 10   # keep doing that every 10 minutes
oneloop dashboard            # http://127.0.0.1:4747
```

When something needs you:

```sh
oneloop status
oneloop approve 123
oneloop sendback 123 "also handle empty input"
oneloop resume 123           # after you fixed what stopped it
oneloop cancel 123
```

To run one stage on its own while tuning prompts: `oneloop spec|build|checks|verify|review|ship|ci|land 123`. To see what happened: `oneloop logs 123` for events, or `oneloop logs 123 review` for that stage's transcript.

To turn off merging without you: `oneloop pause` stops everything (no new runs, no merges without you), and `oneloop automerge off` does it for one repo. Both are also buttons on the dashboard.

## Configure a repo

oneloop reads its rules from the **base branch only**: a change can never edit the rules that judge it. A diff that touches any of these files is high risk, so it needs your approval.

| File | Purpose |
|---|---|
| `.oneloop.toml` | check commands, risk paths, caps, budgets, models, gate modes, auto-merge level |
| `.claude/skills/verify/SKILL.md` | how to start, drive and inspect the running app; the verifier follows it |
| `REVIEW.md` | what a reviewer of this repo should look for |
| `.oneloop/checklist.md` | reliability checks, each with a trigger read from the diff |
| `.oneloop/prompts/<stage>.md` | extra guidance appended to a stage's prompt |

A minimal `.oneloop.toml`:

```toml
[checks]
lint = "npm run lint"
test = "npm test --silent"

[risk]
sensitive = ["src/auth/**", "src/billing/**"]

[land]
auto_merge = "low"     # off | low | medium

[gates]                # off | observe | enforce
checklist = "observe"  # start a new gate in observe mode; promote it once its false positives are gone
```

All keys and their defaults are in [`oneloop/config.py`](oneloop/config.py). An unknown key is an error, so a typo cannot quietly fall back to a default.

A checklist item:

```
## RL-001: New session fields load from old transcripts
trigger: path src/session/**, added /JSON\.parse\(/
check: Confirm a new field loads safely when older transcripts lack it.
origin: #482
```

`oneloop learn` proposes new items from what went wrong: patterns found in at least two PRs and readable from a diff. It also proposes removing items that never fire. Both come to you as a PR.

## How it stays honest

- **Separate sessions, separate knowledge.** The verifier and reviewer see the spec and the code, never the builder's notes or transcript.
- **Pass, fail or couldn't-run.** A failed read is never a quiet success. If CI reports zero checks, that is couldn't-run, not green.
- **Pinned to a commit.** Verify and review record the SHA they judged. LAND merges with `--match-head-commit`, and only when that SHA is still the PR head. After a rebase, a review carries over only if the patch-id is unchanged; verify always runs again.
- **Live re-check before merging.** LAND checks the PR head, CI, new comments, change requests, the hold label and the issue state.
- **Confirmed writes.** Every push, comment, label and merge follows four steps: record the intent, act, confirm by reading it back, record it as done. A write that cannot be confirmed is marked "maybe" and confirmed before anything is retried, so nothing is posted twice.
- **Stamped merges.** The squash commit carries `Oneloop-Verified: verify e2e 3/3; checks lint,test; review r2 clean; risk low; merge auto @abc1234`.
- **Untrusted issue text.** Prompts treat issue text as data. Queue mode only picks up issues labeled by someone with write access.

**Limits.** Tool deny rules match command text, so they are a guard rail, not a sandbox. The real guarantees come from oneloop owning every git and GitHub write and checking the worktree and remote after each session. Costs shown are API list price; on a subscription that is not what you are billed.

## Dashboard and mod

`oneloop dashboard` serves a local page:
- **Inbox** of runs that need you, with one-key approve.
- **Live runs**, showing each run's stage strip and current activity.
- **Run detail page** for each run:
  - timeline split into agent, CI and waiting-on-you time
  - cost by stage
  - acceptance criteria against the verifier's evidence
  - review rounds with validator verdicts
  - risk reasons
  - every GitHub write
- **Metrics**: human touches per merged PR, auto-merge rate, time to merge, cost per PR by stage, and quality guards (reverts, rejections, validator drop rate, verify couldn't-run rate).

The page listens on 127.0.0.1 only. It checks the Host header on every request, and each action needs a token generated when the server starts.

The Claude Code mod in [`mod/`](mod/) puts the same inbox inside your session:
- a status line
- one toast when a run starts needing you
- `/oneloop`, which opens a pane where key 1 approves or resumes, 2 opens the PR and 3 opens the dashboard

Install it with:

```
/plugin marketplace add pxc0de/oneloop
/plugin install oneloop@oneloop
```

## Development

```sh
python3 -m unittest discover -t . -s tests        # whole loop offline: fake claude, in-memory GitHub, real git
python3 -m tests.demo /tmp/oneloop-demo && ONELOOP_HOME=/tmp/oneloop-demo oneloop dashboard
claude plugin test mod
```

All state lives in `~/.oneloop/` (or `$ONELOOP_HOME`):
- `runs/<owner>__<repo>/<issue>/`: `state.json`, `events.jsonl`, artifacts, session transcripts and evidence
- `worktrees/`
- `ledger.jsonl`
- `control.json`
