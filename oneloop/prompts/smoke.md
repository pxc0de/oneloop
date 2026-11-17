<!-- oneloop:stage=verify -->
# Verify-skill smoke test

Use this repository's verify skill to start the app, exercise one basic flow end to end, and stop it again. You are testing the skill, not a change: report whether its steps work exactly as written.

- **pass**: every step worked as written and you observed the app working.
- **fail**: a step did not work as written. Say which and what happened.
- **couldnt_run**: the environment prevented it (missing tool, port in use). Say what.

Do not modify files in the current directory. Put scratch files in `{{evidence_dir}}`.

## The verify skill

{{verify_skill}}
