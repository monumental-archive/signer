# signer

<!-- badges:begin -->
[![ci](https://github.com/monumental-archive/signer/actions/workflows/gate.yml/badge.svg)](https://github.com/monumental-archive/signer/actions/workflows/gate.yml)
[![OpenSSF Scorecard](https://api.scorecard.dev/projects/github.com/monumental-archive/signer/badge)](https://scorecard.dev/viewer/?uri=github.com/monumental-archive/signer)
[![SLSA Build L3](https://img.shields.io/badge/SLSA-Build%20L3-2ea44f)](https://github.com/monumental-archive/.github/blob/main/docs/runbook.md#verifying-as-a-consumer-would)
[![SLSA Source L3](https://img.shields.io/badge/SLSA-Source%20L3-2ea44f)](https://github.com/monumental-archive/.github/blob/main/docs/source-track.md)
[![SLSA Dependencies L2](https://img.shields.io/badge/SLSA-Dependencies%20L2-2ea44f)](https://github.com/monumental-archive/.github/blob/main/docs/dependency-track.md)
<!-- pending (human step): OpenSSF Best Practices — answer the form from docs/best-practices.md, then set 'bestpractices <BP_ID>' in .badge-states and re-run fix:badges -->
<!-- pending (human step): REUSE — register at api.reuse.software (FSFE login), then set 'reuse registered' in .badge-states and re-run fix:badges -->
[![fair-software](https://img.shields.io/badge/fair--software.eu-%E2%97%8F%20%E2%97%8F%20%E2%97%8B%20%E2%97%8B%20%E2%97%8B-orange)](https://fair-software.eu)
<!-- badges:end -->

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

## What lives here

One signing workflow, [`sign.yml`](.github/workflows/sign.yml), plus the
org CI gate stub, and nothing else — a change to the set of files in this
repository is a design event, not housekeeping.

`sign.yml` takes exactly one subject shape per call — a `sha256sum`
manifest for file artifacts, or a name-plus-digest pair for an OCI image —
delivered as declared input values, never through the artifact store. It
holds `id-token: write`, `attestations: write` and
`artifact-metadata: write`, and never `contents: write`: a signer that can
publish is a signer that can be made to publish.

Beyond build provenance it signs **allowlisted predicate claims** (the
artifact VSA and OpenVEX today): the case statement in its validate step
is the org's entire signing surface, enumerable by reading the one file,
and growing it is a reviewed change here. Every claim type verifies
against the same identity; consumers vary only `--predicate-type`.

The full canon — orchestration, ordering, the stranger's verification
one-liners — is `docs/release.md` and `docs/runbook.md` in
[`.github`](https://github.com/monumental-archive/.github); the design
history is issues #28 and #107 there.
