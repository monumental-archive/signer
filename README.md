# trusted-builder

Shared GitHub Actions workflows for every Monumental Archive repository, and the
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
| **`attest.yml`**, **`attest-oci.yml`** — the trusted builders | **yes** | **never** |
| Everything else (shared release/CI helpers) | **never** | yes |

Neither signing workflow performs an `actions/checkout` — not of this
repository and emphatically not of the caller's — and neither runs caller
scripts. What crosses the boundary is a text file of digests, or a name and
a digest. Every step is a SHA-pinned first-party action.

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
either signing workflow silently drops every consumer of this repository
from Build L3 to Build L2.** Nothing will go red. Treat those two files as
append-only-with-argument: changes to them need a stated reason in the PR.

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
    uses: monumental-archive/trusted-builder/.github/workflows/attest.yml@<commit-sha>
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

### `attest-oci.yml` — sign provenance over an image

Takes `subject-name` (no tag, no digest) and `subject-digest`, validates both,
and signs. Separate from `attest.yml` because the subject shapes genuinely
differ: a checksums file yields many subjects, an image is exactly one.

`push-to-registry` defaults to **false**. Turning it on also writes the
attestation into the registry as an OCI referrer, which registry-native
tooling discovers without GitHub's API — but it requires the caller to grant
`packages: write`, handing a shared signing workflow write access to its
registry. That is a per-repository decision, not a default.

### `verify.yml` / `verify-oci.yml` — verify as a consumer would

`verify.yml` downloads artifacts and verifies each file; `verify-oci.yml`
verifies an image by name and digest. Both run `gh attestation verify` with
every pin the published instructions name, and both hold **no** `id-token`:
they handle artifacts, so they must not be able to sign.

`verify-oci.yml` takes `bundle-from-oci`, which reads the attestation from
the OCI referrer rather than GitHub's attestation API. That matters more than
it looks: **attestations do not follow a repository transfer** — after one,
the API lookup 404s under both the old and the new owner — whereas the
registry referrer is independent of who owns the repo.

## Adopting it

Two shapes. Both keep publishing in your repo — that never moves, because
`cargo publish` and `docker build` execute code from your dependency tree and
must not share a job with the signing identity.

### Files (crates, tarballs, npm)

Your build job produces a `sha256sum`-format checksums file and uploads it
**alone** as an artifact, then:

```yaml
  attest:
    needs: build
    permissions:
      id-token: write        # exactly these three
      attestations: write
      contents: read
    uses: monumental-archive/trusted-builder/.github/workflows/attest.yml@<sha>
    with:
      subjects-artifact: subjects
      subjects-file: SUBJECTS.sha256
      subjects-digest: ${{ needs.build.outputs.subjects-digest }}

  verify:
    needs: [build, attest]
    permissions:
      contents: read         # and nothing else
    uses: monumental-archive/trusted-builder/.github/workflows/verify.yml@<sha>
    with:
      artifacts-artifact: dist
      source-repo: ${{ github.repository }}
      source-ref: ${{ github.ref }}
      source-digest: ${{ github.sha }}
      signer-workflow: monumental-archive/trusted-builder/.github/workflows/attest.yml
      signer-digest: <sha>
```

### Images

Push by digest, then hand over the name and digest:

```yaml
  attest:
    needs: push
    permissions:
      id-token: write
      attestations: write
      contents: read
    uses: monumental-archive/trusted-builder/.github/workflows/attest-oci.yml@<sha>
    with:
      subject-name: ghcr.io/monumental-archive/<image>
      subject-digest: ${{ needs.push.outputs.digest }}
      push-to-registry: true   # see below

  verify:
    needs: [push, attest]
    permissions:
      contents: read
      packages: read
    uses: monumental-archive/trusted-builder/.github/workflows/verify-oci.yml@<sha>
    with:
      image: ghcr.io/monumental-archive/<image>
      digest: ${{ needs.push.outputs.digest }}
      source-repo: ${{ github.repository }}
      source-ref: ${{ github.ref }}
      source-digest: ${{ github.sha }}
      signer-workflow: monumental-archive/trusted-builder/.github/workflows/attest-oci.yml
      signer-digest: <sha>
      bundle-from-oci: true
```

`push-to-registry: true` defaults off because it forces you to grant
`packages: write` to a shared signing workflow. Turn it on when consumers
gate on verification: it writes the attestation into the registry, which
`bundle-from-oci` then reads — and that path survives a repository transfer,
where the API lookup does not.

### Three things that will bite

1. **Grant exactly the scopes listed.** A called workflow may only downgrade
   the caller's grant; asking for one you withheld kills the run as
   `startup_failure` — no jobs, no annotations, no log, and `actionlint`
   cannot see it because the contract spans two repositories.
2. **Pin by commit SHA.** `uses:` accepts no contexts or expressions, so a
   release tag freezes whichever SHA you wrote, permanently.
3. **Your published verification command changes.** `--repo <you>` alone
   stops working, because with no signer flag `gh` defaults its identity
   regex to the source repo, which no longer matches the signer. Update
   README and SECURITY.md in the same release.

## Verifying, downstream

```bash
gh attestation verify <artifact> \
  --repo <owner>/<source-repo> \
  --source-ref refs/tags/v<version> \
  --source-digest <tagged-commit-sha> \
  --signer-workflow monumental-archive/trusted-builder/.github/workflows/attest.yml \
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
