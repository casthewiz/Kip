# generic provider

## Handles
Any claim, when no more specific provider applies or one fell through.
Libraries, CLIs, scripts, build config, pure logic.

## Requires
Nothing. Always available. This is the floor of the cascade.

## lite
Typecheck, build, or lint the touched files. Grep or diff for static claims
("the export was removed").

## full
Run the existing test that covers the claim. If none exists, write the
smallest check that fails if the claim is false (an assert-based script or
one small test file) and run it. For CLIs, run the command with real input
and inspect output and exit code.

## ultra
Everything in full, plus:
- Falsifiability: stash or revert the change (`git stash`), run the check,
  confirm it fails, restore (`git stash pop`), confirm it passes.
- Edge cases: empty, null, boundary, and malformed inputs for the changed
  logic.
- Siblings: grep every caller of changed functions and run their tests too.
- Full suite for the touched package, not just the targeted test.

## Observation
The exact command, exit code, and pass/fail counts. For falsifiability, both
runs: "red without change (1 failed), green with (1 passed)".
