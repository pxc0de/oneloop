<!-- oneloop:stage=build -->
# Stage: BUILD (fix round {{round}})

Your change came back. The items below come from {{sources}}. The people and sessions who found them did not see your reasoning, only the result. Fix each item at its root cause, then run the checks again: {{check_commands}}.

{{rethink}}

## Items to address

{{items}}

## Rules (unchanged)

- Do not commit, push, switch branches or rewrite history, and do not use `gh`.
- Do not edit: {{protected}}.
- Never skip, sleep, retry or delete tests to get green.
- If you disagree with an item, do not change code for it. List it in `not_addressed` with the reason; a person will see it.

Report which item ids you addressed.
