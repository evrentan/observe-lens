# Pipeline knowledge notes

Write down what an agent cannot guess about your CI. Delete these prompts as
you fill them in.

## Systems and instances

- Which `config list` instance builds what? (e.g. ci-main = everything, github-main = only the shop repo)

## Job / workflow map

- Which job builds which service? Which jobs chain (build -> deploy -> tests)?
- Job naming conventions and the folders jobs live in.

## Tests

- Which jobs run sanity/regression tests and against which environment?
- Known flaky tests and their tickets.
- Where test reports are published (artifact names, Allure or JUnit).

## Typical durations

- How long does each job normally take? (helps spot a stuck build)

## Ids and conventions

- How commits map to image tags and artifact names.
