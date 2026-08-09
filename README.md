# signer

The org's signing identity, and nothing else.

This repository holds one job: receive artifact digests from a caller and
sign them. It runs **no caller-supplied code** — no checkout of any
repository, no caller scripts, no build. That is the whole reason it exists
as a separate repository, and the whole reason the org's provenance means
anything.

## Why it is separate

SLSA provenance records a `builder.id`, which the [GitHub Actions
buildType](https://github.com/actions/buildtypes) defines as *the entity
that generated the provenance* — the workflow that assembled and signed it,
not the one that compiled the bytes. When signing happens inside a reusable
workflow, that workflow becomes the `builder.id`.

So every artifact the org publishes will name a workflow **here** as its
builder, and consumers verify against that identity rather than against the
repository that built the bytes.

SLSA Build L3 requires that the signing identity be unreachable from
user-defined build steps. A repository that both signs and runs caller code
cannot offer that. Hence the split: builds live in the shared workflows in
[`.github`](https://github.com/monumental-archive/.github), signing lives
here.

## The name is now permanent

`job_workflow_ref` becomes the Fulcio certificate identity of every artifact
the org ships, and Sigstore is append-only. Renaming this repository or
moving its signing workflow breaks verification for everything previously
published, unfixably.

It was renamed from `trusted-builder` before anything was published, because
that name described a builder and this repository has never built anything.
That window is closed.

## Current state: stripped, pending phase-2 canon

The previous generation of workflows has been removed. They were written for
one project's pipeline, and the release design in
[issue #28](https://github.com/monumental-archive/.github/issues/28)
rebuilds this repository from the specification rather than carrying them
forward.

What arrives here when phase-2 canon is designed:

- **one** signing workflow, taking a subject manifest and nothing else
- no `actions/download-artifact`, no checkout of a caller, no `run:` over
  caller-controlled values
- `id-token: write`, `attestations: write`, `artifact-metadata: write`, and
  never `contents: write`

Until then this repository conforms to the org gate and holds no logic.
The canon is `docs/release.md` and `docs/slsa-reference.md` in
[`.github`](https://github.com/monumental-archive/.github).
