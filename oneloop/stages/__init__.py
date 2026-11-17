from oneloop.stages import build, checks_stage, ci, land, review, ship, spec, verify

RUNNERS = {
    "spec": spec.run,
    "build": build.run,
    "checks": checks_stage.run,
    "verify": verify.run,
    "review": review.run,
    "ship": ship.run,
    "ci": ci.run,
    "land": land.run,
}
