# trusted-builder

Shared GitHub Actions workflows for every `CarlAllenn` repository, and the
SLSA v1.0 **Build L3** signing boundary for the ones that publish artifacts.

Consumers verify a release against a workflow in *this* repository rather
than against the repository that built it. That is the whole point: the
signing identity lives somewhere the publishing project cannot modify, so a
compromise of the project cannot produce provenance that verifies.

## The one rule

This repository has two zones, and the rule that separates them is not
stylistic — it is the security property.

| Zone | Holds `id-token` | May run caller-supplied code |
| --- | --- | --- |
| **`attest.yml`** — the trusted builder | **yes** | **never** |
| Everything else (shared release/CI helpers) | **never** | yes |

`attest.yml` performs no `actions/checkout` — not of this repository and
emphatically not of the caller's — runs no caller scripts, and receives
exactly one thing across the boundary: a text file of digests. Four steps,
all SHA-pinned first-party actions.

The reason is mechanical. Inside a called workflow the `github` context is
the **caller's**, so a bare `actions/checkout` here fetches the *calling*
repository at its tag, and any `run:` after it executes code the caller
controls — in the same job as the OIDC token, which any step can retrieve
via `$ACTIONS_ID_TOKEN_REQUEST_TOKEN`. That is precisely the property the
boundary exists to remove. `gh attestation verify --help` states the
criterion:

> have the build and attestation signing occur within a reusable workflow
> whose execution cannot be influenced by input provided through the caller
> workflow

and SLSA v1.0 states the property:

> Any secret material used for authenticating the provenance […] MUST NOT be
> accessible to the environment running the user-defined build steps.

**Adding a `checkout`, a `run:` over caller data, or `contents: write` to
`attest.yml` silently drops every consumer of this repository from Build L3
to Build L2.** Nothing will go red. Treat that file as append-only-with-
argument: changes to it need a stated reason in the PR.

## How L3 is actually achieved here

Not by making the OIDC token unreachable — it isn't. A calling repository
still needs `id-token: write` of its own for registry trusted publishing,
and a compromised build step there can still obtain a Fulcio certificate.
But that certificate bears the **caller's** workflow identity, and every
documented verification pins `--signer-workflow` to this repository. The
reachable token is not the one that counts.

## Workflows

### `attest.yml` — sign provenance over declared subjects

Takes an Actions artifact containing a `sha256sum`-format checksums file plus
that file's own SHA-256, verifies the file survived transport, and attests
every subject in it. `actions/attest`'s `subject-checksums` input derives the
subjects from the digests, so the artifact bytes never travel here.

```yaml
jobs:
  attest:
    permissions:
      id-token: write        # exactly these three, no more and no less
      attestations: write
      contents: read
    uses: CarlAllenn/trusted-builder/.github/workflows/attest.yml@<commit-sha>
    with:
      subjects-artifact: subjects
      subjects-file: SUBJECTS.sha256
      subjects-digest: ${{ needs.build.outputs.subjects-digest }}
```

`verify.yml` needs `contents: read` and nothing else.

**Grant exactly the listed scopes.** A called workflow may only *downgrade*
the caller's grant, never elevate it, so if a workflow here requests a scope
the caller withheld, the run dies as `startup_failure` — **no jobs, no
annotations, no log**. Nothing catches it locally either: `actionlint`
validates each repository in isolation and this contract spans both. That
failure cost the first lab run, which is what a lab is for.

Pin by **commit SHA**. `uses:` accepts no contexts or expressions, so the
pin is frozen into whatever ref the caller runs from — a tag, for a release.
A bug in this workflow is therefore permanent for any tag that already
references it, which is why it is kept as small as it is.

### `verify.yml` — verify as a consumer would

Downloads artifacts and runs `gh attestation verify` with every pin the
published instructions name. Holds **no** `id-token`: it handles bytes, so it
must not be able to sign.

## Verifying, downstream

```bash
gh attestation verify <artifact> \
  --repo <owner>/<source-repo> \
  --source-ref refs/tags/v<version> \
  --source-digest <tagged-commit-sha> \
  --signer-workflow CarlAllenn/trusted-builder/.github/workflows/attest.yml \
  --signer-digest <builder-commit-sha> \
  --deny-self-hosted-runners
```

**Pin the workflow, never the repository.** `gh` also offers `--signer-repo`,
which compiles to a prefix regex over the whole repository — so it would
accept a signature from *any* workflow here, including a shared release-half
workflow that legitimately runs caller code under the caller's own OIDC.
File-level pinning is what keeps the two zones apart on the verifier's side.
`--signer-repo` is safe only for a builder repository that contains nothing
but signing workflows, and this one is not that.

`--signer-workflow` compiles to a *prefix* regex over the certificate SAN, so
it pins the workflow's path and **not** its ref — anyone able to push a
branch here would produce a matching signer identity. `--signer-digest` pins
`job_workflow_sha` and is what actually closes that. It is also why this
repository carries tag-immutability and signed-commit rulesets: a shared
signer concentrates the risk of all its callers into one place.

## What this does not do

The subject digests are computed by the **caller's** build. A compromised
build can ask this workflow to sign a digest of its choosing, and it will.
SLSA puts that outside the Build track — "the SLSA level of an artifact is
independent of the level of its dependencies" — and the leg that closes it is
reproducible builds, not further hardening here. Stated so it is scope rather
than an unnoticed gap.
