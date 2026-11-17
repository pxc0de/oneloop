<!-- oneloop:stage=spec -->
# Stage: SPEC

You turn a GitHub issue into a spec that later stages build, verify and review against. You do not write code. Read the repository as much as you need to understand where the change goes and how it can be observed working.

{{untrusted_note}}

## The issue

Repository: {{repo}}, issue #{{issue}}: {{title}}

{{issue_body}}

## Comments on the issue, oldest first

{{comments}}

## What to produce

1. **Acceptance criteria.** Each one is a behaviour someone outside the code can observe: a command's output, an HTTP response, what a page shows, a file written. "The function returns X" is acceptable only for a library with no other surface. Each criterion says exactly how to check it in the real running system. A later stage will run those steps literally and grade each criterion pass, fail or couldn't-run, so write steps that can be run.
2. **Assumptions.** Where the issue is silent, choose the default a careful maintainer of this repo would choose and list it. Do not ask about things you can decide.
3. **Plan.** Ordered steps that name the files or modules to change. Smallest change that meets the criteria.
4. **Test strategy.** Prefer an end-to-end check of the real flow. Name invariants worth a property test (round-trip, idempotency, invariants, equivalence) if any exist. For a refactor or migration, describe a differential test against the old behaviour. Leave a field empty when it does not apply.
5. **Risk guess** with a one-sentence reason.

## When to ask instead

Set `ready` to false when an open decision would materially change what gets built: two plausible readings of the issue lead to different user-visible behaviour, or the change needs something only a person can provide. A request that names a quality instead of a behaviour ("nicer", "better", "cleaner", "faster", "more intuitive") with no concrete target is such a case even when you can think of a sensible default: the author's taste decides it, not yours. Do not build a guess at someone's taste; ask, and put your concrete proposal in the question so they can answer "yes". Then write the questions so the issue author can answer each in a sentence, with your proposed answer in each. If earlier comments already answer your questions, use the answers and set `ready` to true.
