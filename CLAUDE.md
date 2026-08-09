# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with
code in this repository.

## What this is

Shared GitHub Actions workflows for every `CarlAllenn` repository, and — in
`attest.yml` alone — the SLSA v1.0 **Build L3** signing boundary for the ones
that publish artifacts. Callers verify releases against a workflow *here*
rather than against the repository that built the bytes.

Design rationale, the paid-for defects behind it, and the verification
semantics live in [README.md](README.md). Read it before changing anything;
this repository is short but almost every line encodes a specific finding.

## The rule that must not be broken

Two zones:

| Zone | Holds `id-token` | May run caller-supplied code |
| --- | --- | --- |
| `attest.yml`, `attest-oci.yml` | **yes** | **never** |
| everything else | **never** | yes |

`attest.yml` performs **no `actions/checkout`** — not of this repository, not
of the caller's — runs no caller scripts, no build, no `cargo`, no `npm`. One
`sha256sum`-format text file crosses the boundary and `actions/attest` derives
every subject from it via `subject-checksums`.

Adding a checkout, a `run:` over caller data, or `contents: write` to that
file **silently drops every consumer from Build L3 to Build L2**. Nothing goes
red. Any change to `attest.yml` needs a stated reason.

Inverse rule, equally load-bearing: any workflow here that *does* run caller
code must never be granted `id-token: write`, because a certificate minted in
it would bear this repository's identity.

## Verification pins: workflow, never repo

Consumers must pin `--signer-workflow <owner>/trusted-builder/.github/workflows/attest.yml`,
**never** `--signer-repo <owner>/trusted-builder`.

`--signer-repo` compiles to a prefix regex over the whole repository, so it
would accept a signature from *any* workflow here — including a shared
release-half workflow that legitimately runs caller code. File-level pinning
is what keeps the two zones apart on the verifier's side. `--signer-workflow`
is itself only a path prefix (the `@ref` is unpinned), so releases should also
pass `--signer-digest` to pin `job_workflow_sha`.

## Conventions

- Every `uses:` pinned to a full commit SHA with a trailing `# vX.Y.Z`
  comment. `uses:` accepts no contexts or expressions, so a caller's pin is
  frozen into whatever ref it runs from — for a release, a tag, permanently.
  A bug shipped here is permanent for every tag already referencing it. Keep
  `attest.yml` small for that reason alone.
- Caller-supplied values are routed through `env:` with an `UNTRUSTED_`
  prefix and never expanded into `run:` code (zizmor template-injection).
- Spelling registers match the edtf canon: **en-US in code and identifiers**,
  **en-GB in prose**.
- Commits are SSH-signed; conventional commits.

## Testing

Changes are exercised from
[edtf-release-lab](https://github.com/CarlAllenn/edtf-release-lab) — dummy
crates, real GitHub APIs, no registry publishes — before any production
repository moves its pin. The release half of a pipeline is covered by
nothing except live releases, which is the most expensive place to find a
defect; that is what the lab exists to avoid.

## Consumers

`edtf`, and by intent `iiif-server`, `monumental-archive-db`, `j2k`. Moving a
consumer's pin means updating its verification snippets too — README,
SECURITY.md, any in-pipeline self-verify, and any downstream repo that checks
its artifacts.
