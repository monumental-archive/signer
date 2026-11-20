#!/usr/bin/env python3
"""Run sign.yml's input step, cut from the workflow file, over its guards.

WHAT RUNS. The `run:` text of the step "Validate and materialise the
subject manifest" (id `manifest`), read out of
.github/workflows/sign.yml at test time and executed as the runner
executes it: `bash --noprofile --norc -eo pipefail <file>`, with every
input the step's `env:` declares set (the runner sets an undeclared
input to "", and `bundle-artifact` to its default) and RUNNER_TEMP,
GITHUB_OUTPUT and GITHUB_ENV pointed into a temporary directory per
row. `jq` and `tr` are the real ones on PATH.

Why the step and not a script: the signer runs nothing it did not carry
in its own pinned file (no checkout, no action, no download; see the
header of sign.yml), so the check cannot live in a helper the step
loads. The thing tested is the step itself, row for row
(monumental-archive/signer#90). If the step is renamed, loses its id,
or stops being a literal `run: |` block, extraction fails by name
rather than letting the suite pass on nothing.

A refusal row asserts exit 1, the exact `::error::` line, nothing on
stderr, and exactly what the step had written before it refused. The
step writes as it goes, so a refusal can leave earlier outputs behind.
The row pins that state, and none of them leaves SUBJECTS_FILE or
PREDICATE_FILE in the job environment. A passing row asserts every
output, every environment line and every byte of every file.

One guard is not reachable: "no subjects declared". A subject list that
is empty or only whitespace is refused by the shape check before the
count, and any other list has a non-empty line, which is counted. The
row that would reach it asserts the shape refusal instead.

The 1,024- and 1,025-subject rows need bash 4 or later. On bash 3.2
(macOS) the step's `${var//[[:space:]]/}` takes 16 s for 100 lines and
grows faster than quadratically, so outside CI those two rows are
skipped on bash 3. Under CI they always run.

stdlib `unittest`. Run through the gate as `mise run test`, which `ci`
collects.
"""

import json
import os
import shutil
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parent.parent
WORKFLOW = ROOT / ".github" / "workflows" / "sign.yml"
STEP_NAME = "Validate and materialise the subject manifest"
STEP_ID = "manifest"
BASH = shutil.which("bash")

OPENVEX = "https://openvex.dev/ns/v0.2.0"
DIGEST = "sha256:" + "a" * 64
IMAGE = "ghcr.io/monumental-archive/example"
# The workflow_call defaults: every input "" but bundle-artifact.
DEFAULTS = {
    "UNTRUSTED_SUBJECTS": "",
    "UNTRUSTED_IMAGE_NAME": "",
    "UNTRUSTED_IMAGE_DIGEST": "",
    "UNTRUSTED_PREDICATE_TYPE": "",
    "UNTRUSTED_PREDICATE": "",
    "UNTRUSTED_BUNDLE_ARTIFACT": "attestation-bundle",
}
LARGE = 1000  # subject rows above this many lines need bash 4
BASH_FAST = 4  # the first major version that runs them in seconds
# What an assertion expects when nothing was exported or written: typed,
# because a bare `{}` or `[]` handed to assertEqual is read as Any.
NO_ENVIRONMENT: dict[str, str] = {}
NO_FILES: list[str] = []


class StepNotFoundError(Exception):
    """sign.yml no longer holds the step this suite tests, as it is named."""


def indent(line: str) -> int:
    """Measure a line's leading spaces.

    Returns:
        The count of spaces before the first other character.

    """
    return len(line) - len(line.lstrip(" "))


def step_lines(text: str) -> tuple[list[str], int]:
    """Find the step by its name, which must be unique.

    Returns:
        The step's lines after its `- name:` line, and the indentation
        of its keys.

    Raises:
        StepNotFoundError: no step, or more than one, has the name.

    """
    lines = text.split("\n")
    starts = [
        i for i, line in enumerate(lines) if line.strip() == f"- name: {STEP_NAME}"
    ]
    if len(starts) != 1:
        msg = f"sign.yml holds {len(starts)} steps named '{STEP_NAME}', not 1"
        raise StepNotFoundError(msg)
    start = starts[0]
    dash = indent(lines[start])
    end = start + 1
    while end < len(lines) and (not lines[end].strip() or indent(lines[end]) > dash):
        end += 1
    return lines[start + 1 : end], dash + 2


def key_at(step: list[str], key: int, name: str) -> int:
    """Find the step's one `name:` key.

    Returns:
        Its index in `step`.

    Raises:
        StepNotFoundError: the step has no such key, or two.

    """
    found = [
        i
        for i, line in enumerate(step)
        if indent(line) == key and line.strip().startswith(f"{name}:")
    ]
    if len(found) != 1:
        msg = f"step '{STEP_NAME}' has {len(found)} `{name}:` keys, not 1"
        raise StepNotFoundError(msg)
    return found[0]


def nested(step: list[str], key: int, at: int) -> list[str]:
    """Take the lines nested under the key at `at`.

    Returns:
        Every line after it, blank lines included, up to the next line
        indented no deeper than the key; trailing blank lines dropped.

    """
    block = []
    for line in step[at + 1 :]:
        if line.strip() and indent(line) <= key:
            break
        block.append(line)
    while block and not block[-1].strip():
        block.pop()
    return block


def extract_step(text: str) -> tuple[str, list[str]]:
    """Cut the input step out of the workflow text.

    Returns:
        The step's `run:` block as the runner receives it, and the names
        its `env:` declares, in order.

    Raises:
        StepNotFoundError: the step, its id, its env or its literal run
            block is missing, or the name is not unique.

    """
    step, key = step_lines(text)
    if step[key_at(step, key, "id")].strip() != f"id: {STEP_ID}":
        msg = f"step '{STEP_NAME}' is not `id: {STEP_ID}`"
        raise StepNotFoundError(msg)
    names = [
        line.strip().split(":", 1)[0]
        for line in nested(step, key, key_at(step, key, "env"))
    ]
    run_at = key_at(step, key, "run")
    block = nested(step, key, run_at)
    if step[run_at].strip() != "run: |" or not block:
        msg = f"step '{STEP_NAME}' has no literal `run: |` block"
        raise StepNotFoundError(msg)
    margin = indent(block[0])
    return "\n".join(line[margin:] for line in block) + "\n", names


def parsed(text: str) -> object:
    """Read one JSON document as an object, to compare two by value.

    Returns:
        The decoded value, unnarrowed: the tests only compare it.

    """
    value: object = json.loads(text)
    return value


def manifest(count: int, ending: str = "\n") -> str:
    """Build a subject list of distinct sha256sum records.

    Returns:
        `count` records, each ended by `ending`.

    """
    return "".join(
        f"{index:064x}  artifact-{index}.tar.gz{ending}" for index in range(count)
    )


def bash_major() -> int:
    """Ask the bash on PATH for its major version.

    Returns:
        BASH_VERSINFO[0], or 0 when there is no bash.

    """
    if BASH is None:
        return 0
    done = subprocess.run(
        [BASH, "-c", 'printf %s "${BASH_VERSINFO[0]}"'],
        capture_output=True,
        text=True,
        check=True,
    )
    return int(done.stdout)


class Run:
    """One run of the step, in a temporary directory removed with the test."""

    def __init__(self, case: unittest.TestCase, inputs: dict[str, str]) -> None:
        """Run the extracted step with `inputs` over the runner's defaults."""
        if BASH is None:
            case.fail("no bash on PATH: the step is a bash script")
        if (
            len(inputs.get("UNTRUSTED_SUBJECTS", "").split("\n")) > LARGE
            and bash_major() < BASH_FAST
        ):
            if os.environ.get("CI"):
                case.fail("bash 3 under CI: the large rows must run there")
            case.skipTest("bash 3.2 takes minutes on a list this long; CI runs it")
        script, _ = extract_step(WORKFLOW.read_text(encoding="utf-8"))
        directory = TemporaryDirectory()
        case.addCleanup(directory.cleanup)
        root = Path(directory.name)
        self.temp = root / "runner-temp"
        self.temp.mkdir()
        self.output = root / "github-output"
        self.env_file = root / "github-env"
        step = root / "step.sh"
        step.write_text(script, encoding="utf-8")
        env = {
            "PATH": os.environ.get("PATH", ""),
            "LANG": "C.UTF-8",
            "RUNNER_TEMP": str(self.temp),
            "GITHUB_OUTPUT": str(self.output),
            "GITHUB_ENV": str(self.env_file),
        }
        env |= DEFAULTS | inputs
        # The runner's own invocation for `shell: bash`.
        completed = subprocess.run(
            [BASH, "--noprofile", "--norc", "-eo", "pipefail", str(step)],
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        self.code = completed.returncode
        self.stdout = completed.stdout
        self.stderr = completed.stderr

    def outputs(self) -> dict[str, str]:
        """Read the step outputs as written.

        Returns:
            Each `name=value` line of GITHUB_OUTPUT; empty if none.

        """
        if not self.output.exists():
            return {}
        lines = self.output.read_text(encoding="utf-8").splitlines()
        return dict(line.split("=", 1) for line in lines)

    def environment(self) -> dict[str, str]:
        """Read the job environment as written.

        Returns:
            Each `name=value` line of GITHUB_ENV; empty if none.

        """
        if not self.env_file.exists():
            return {}
        lines = self.env_file.read_text(encoding="utf-8").splitlines()
        return dict(line.split("=", 1) for line in lines)

    def files(self) -> list[str]:
        """List what the step left under RUNNER_TEMP.

        Returns:
            The file names, sorted.

        """
        return sorted(path.name for path in self.temp.iterdir())


# What a refusal has written by the time it refuses, by the stage it
# refuses at. The step writes as it goes; none of these is read by an
# attest step, because the job stops at the step's exit 1.
AT_PREDICATE = {"outputs": {"bundle-artifact": "attestation-bundle"}, "files": []}
# A predicate the parser refuses leaves the file its redirect opened,
# empty; PREDICATE_FILE is never exported for it.
AT_PARSE = {
    "outputs": {"bundle-artifact": "attestation-bundle"},
    "files": ["predicate.json"],
}
AT_SUBJECTS = {
    "outputs": {"bundle-artifact": "attestation-bundle", "predicate": "false"},
    "files": [],
}
AT_RECORDS = {
    "outputs": {
        "bundle-artifact": "attestation-bundle",
        "predicate": "false",
        "mode": "files",
    },
    "files": ["subjects.sha256"],
}

REFUSALS = [
    (
        "predicate type not on the allowlist",
        {
            "UNTRUSTED_PREDICATE_TYPE": "https://slsa.dev/verification_summary/v1",
            "UNTRUSTED_PREDICATE": "{}",
            "UNTRUSTED_SUBJECTS": manifest(1),
        },
        "predicate-type is not on this signer's allowlist",
        AT_PREDICATE,
    ),
    (
        "predicate with no type is judged by the allowlist",
        {"UNTRUSTED_PREDICATE": "{}", "UNTRUSTED_SUBJECTS": manifest(1)},
        "predicate-type is not on this signer's allowlist",
        AT_PREDICATE,
    ),
    (
        "type with no predicate",
        {"UNTRUSTED_PREDICATE_TYPE": OPENVEX, "UNTRUSTED_SUBJECTS": manifest(1)},
        "predicate-type without a predicate asserts nothing",
        AT_PREDICATE,
    ),
    (
        "type with a whitespace-only predicate",
        {
            "UNTRUSTED_PREDICATE_TYPE": OPENVEX,
            "UNTRUSTED_PREDICATE": " \n\t ",
            "UNTRUSTED_SUBJECTS": manifest(1),
        },
        "predicate-type without a predicate asserts nothing",
        AT_PREDICATE,
    ),
    (
        "predicate is a JSON array",
        {
            "UNTRUSTED_PREDICATE_TYPE": OPENVEX,
            "UNTRUSTED_PREDICATE": "[]",
            "UNTRUSTED_SUBJECTS": manifest(1),
        },
        "predicate is not a single JSON object",
        AT_PARSE,
    ),
    (
        "predicate is not JSON",
        {
            "UNTRUSTED_PREDICATE_TYPE": OPENVEX,
            "UNTRUSTED_PREDICATE": "{not json",
            "UNTRUSTED_SUBJECTS": manifest(1),
        },
        "predicate is not a single JSON object",
        AT_PARSE,
    ),
    (
        "both kinds of subject",
        {
            "UNTRUSTED_SUBJECTS": manifest(1),
            "UNTRUSTED_IMAGE_NAME": IMAGE,
            "UNTRUSTED_IMAGE_DIGEST": DIGEST,
        },
        "give exactly one of subjects, or image-name plus image-digest",
        AT_SUBJECTS,
    ),
    (
        "neither kind of subject",
        {},
        "give exactly one of subjects, or image-name plus image-digest",
        AT_SUBJECTS,
    ),
    (
        "no subjects: a blank list is refused by the shape check first",
        {"UNTRUSTED_SUBJECTS": " \r\n\n\t"},
        "give exactly one of subjects, or image-name plus image-digest",
        AT_SUBJECTS,
    ),
    (
        "image with only a name",
        {"UNTRUSTED_IMAGE_NAME": IMAGE},
        "an image subject needs BOTH image-name and image-digest",
        AT_SUBJECTS,
    ),
    (
        "image with only a digest",
        {"UNTRUSTED_IMAGE_DIGEST": DIGEST},
        "an image subject needs BOTH image-name and image-digest",
        AT_SUBJECTS,
    ),
    (
        "digest without the sha256: prefix",
        {"UNTRUSTED_IMAGE_NAME": IMAGE, "UNTRUSTED_IMAGE_DIGEST": "a" * 64},
        "image-digest must be sha256:<64 lowercase hex>",
        AT_SUBJECTS,
    ),
    (
        "digest in uppercase hex",
        {"UNTRUSTED_IMAGE_NAME": IMAGE, "UNTRUSTED_IMAGE_DIGEST": "sha256:" + "A" * 64},
        "image-digest must be sha256:<64 lowercase hex>",
        AT_SUBJECTS,
    ),
    (
        "digest one hex short",
        {"UNTRUSTED_IMAGE_NAME": IMAGE, "UNTRUSTED_IMAGE_DIGEST": "sha256:" + "a" * 63},
        "image-digest must be sha256:<64 lowercase hex>",
        AT_SUBJECTS,
    ),
    (
        "digest with a trailing newline",
        {"UNTRUSTED_IMAGE_NAME": IMAGE, "UNTRUSTED_IMAGE_DIGEST": DIGEST + "\n"},
        "image-digest must be sha256:<64 lowercase hex>",
        AT_SUBJECTS,
    ),
    (
        "uppercase image name",
        {
            "UNTRUSTED_IMAGE_NAME": "ghcr.io/Monumental-Archive/example",
            "UNTRUSTED_IMAGE_DIGEST": DIGEST,
        },
        "image-name must be lowercase",
        AT_SUBJECTS,
    ),
    (
        "subject line that is not a sha256sum record",
        {"UNTRUSTED_SUBJECTS": manifest(2) + "::warning::not a record\n"},
        "subject line 3 is not a sha256sum record",
        AT_RECORDS,
    ),
    (
        "subject line with one space, not two",
        {"UNTRUSTED_SUBJECTS": "a" * 64 + " name\n"},
        "subject line 1 is not a sha256sum record",
        AT_RECORDS,
    ),
    (
        "subject line in uppercase hex",
        {"UNTRUSTED_SUBJECTS": "A" * 64 + "  name\n"},
        "subject line 1 is not a sha256sum record",
        AT_RECORDS,
    ),
    (
        "subject line with no name",
        {"UNTRUSTED_SUBJECTS": "a" * 64 + "  \n"},
        "subject line 1 is not a sha256sum record",
        AT_RECORDS,
    ),
    (
        "1,025 subjects",
        {"UNTRUSTED_SUBJECTS": manifest(1025)},
        "1025 subjects exceeds the 1024 an attestation carries",
        AT_RECORDS,
    ),
]


class Extraction(unittest.TestCase):
    """The step this suite runs is the one sign.yml carries."""

    def test_step_is_found_by_name_and_id(self) -> None:
        """Extraction yields a script that begins as the step does."""
        script, _ = extract_step(WORKFLOW.read_text(encoding="utf-8"))
        self.assertTrue(script.startswith("set -euo pipefail\n"), script[:80])

    def test_a_renamed_step_fails_by_name(self) -> None:
        """A rename cannot leave the suite passing on nothing."""
        text = WORKFLOW.read_text(encoding="utf-8").replace(STEP_NAME, "Something else")
        with self.assertRaises(StepNotFoundError) as caught:
            extract_step(text)
        self.assertEqual(
            str(caught.exception),
            f"sign.yml holds 0 steps named '{STEP_NAME}', not 1",
        )

    def test_every_declared_input_is_set_by_the_rows(self) -> None:
        """An input added to the step's env cannot go untested."""
        _, names = extract_step(WORKFLOW.read_text(encoding="utf-8"))
        declared: list[str] = sorted(names)
        expected: list[str] = sorted(DEFAULTS)
        self.assertEqual(declared, expected)

    def test_no_expression_is_expanded_into_the_code(self) -> None:
        """Caller values reach the step through env only, never `${{ }}`."""
        script, _ = extract_step(WORKFLOW.read_text(encoding="utf-8"))
        self.assertNotIn("${{", script)


class Refusals(unittest.TestCase):
    """Every guard refuses with its own message."""

    def test_refusals(self) -> None:
        """Each row: exit 1, the exact message, exactly what was written."""
        for name, inputs, message, written in REFUSALS:
            with self.subTest(name):
                run = Run(self, inputs)
                self.assertEqual(run.code, 1, run.stderr)
                self.assertEqual(run.stdout, f"::error::{message}\n")
                self.assertEqual(run.stderr, "")
                self.assertEqual(run.outputs(), written["outputs"])
                self.assertEqual(run.environment(), NO_ENVIRONMENT)
                self.assertEqual(run.files(), written["files"])

    def test_refusal_does_not_echo_the_offending_value(self) -> None:
        """A caller-controlled value never reaches the log on refusal."""
        hostile = "::add-mask::x"
        for inputs in (
            {
                "UNTRUSTED_PREDICATE_TYPE": hostile,
                "UNTRUSTED_PREDICATE": "{}",
                "UNTRUSTED_SUBJECTS": manifest(1),
            },
            {"UNTRUSTED_SUBJECTS": hostile},
        ):
            with self.subTest(inputs=inputs):
                self.assertNotIn(hostile, Run(self, inputs).stdout)


PREDICATE_MESSAGE = "predicate is not a single JSON object"
# Each refused by the parser (#92). The first three and the numbers were
# accepted by jq 1.8.2, which this step used before.
NOT_ONE_STANDARD_OBJECT = [
    ("two documents", '{}{"a":1}'),
    ("NaN", '{"a":NaN}'),
    ("Infinity", '{"a":Infinity}'),
    ("-Infinity", '{"a":-Infinity}'),
    ("a number no double holds", '{"a":1e1000}'),
    ("a leading-dot number", '{"a":.5}'),
    ("a plus-signed number", '{"a":+1}'),
    ("a leading zero", '{"a":01}'),
    ("an array", "[]"),
    ("a string", '"s"'),
    ("an object then garbage", '{"a":1} x'),
]


class PredicateJson(unittest.TestCase):
    """The predicate is exactly one object in standard JSON (#92)."""

    def test_predicate_must_be_one_standard_object(self) -> None:
        """Refuse anything else; write one object in its compact form."""
        for name, predicate in NOT_ONE_STANDARD_OBJECT:
            with self.subTest(name):
                run = Run(
                    self,
                    {
                        "UNTRUSTED_PREDICATE_TYPE": OPENVEX,
                        "UNTRUSTED_PREDICATE": predicate,
                        "UNTRUSTED_SUBJECTS": manifest(1),
                    },
                )
                self.assertEqual(run.code, 1, run.stderr)
                self.assertEqual(run.stdout, f"::error::{PREDICATE_MESSAGE}\n")
                self.assertEqual(run.stderr, "")
                self.assertEqual(run.environment(), NO_ENVIRONMENT)
                self.assertEqual(
                    (run.temp / "predicate.json").read_text(encoding="utf-8"), ""
                )
        with self.subTest("an empty input"):
            # Refused before the parser, by the presence check.
            run = Run(
                self,
                {
                    "UNTRUSTED_PREDICATE_TYPE": OPENVEX,
                    "UNTRUSTED_SUBJECTS": manifest(1),
                },
            )
            self.assertEqual(
                run.stdout,
                "::error::predicate-type without a predicate asserts nothing\n",
            )
        with self.subTest("one object"):
            # Key order kept, whitespace dropped, a big integer exact,
            # non-ASCII escaped whether it was sent raw or escaped.
            sent = (
                '{\n  "b": [1, 2.5, 100000000000000000001],\n'
                '  "a": "na\u00efve \\u00ef",\n  "c": null\n}\n'
            )
            run = Run(
                self,
                {
                    "UNTRUSTED_PREDICATE_TYPE": OPENVEX,
                    "UNTRUSTED_PREDICATE": sent,
                    "UNTRUSTED_SUBJECTS": manifest(1),
                },
            )
            self.assertEqual(run.code, 0, run.stdout + run.stderr)
            written = (run.temp / "predicate.json").read_text(encoding="utf-8")
            self.assertEqual(
                written,
                '{"b":[1,2.5,100000000000000000001],'
                '"a":"na\\u00efve \\u00ef","c":null}\n',
            )
            self.assertEqual(parsed(written), parsed(sent))


BUNDLE_MESSAGE = "bundle-artifact must be one name of letters, digits, '.', '_' and '-'"
# The names the canon's callers pass (publish.yml, continuous.yml,
# vex-attest.yml at v1.68.7), a matrix value filled in where they
# interpolate one.
CANON_BUNDLES = [
    "attestation-bundle",
    "attestation-bundle-image",
    "attestation-bundle-crates",
    "attestation-bundle-binaries",
    "attestation-bundle-go-binaries",
    "attestation-bundle-source",
    "attestation-bundle-npm",
    "attestation-bundle-extensions",
    "attestation-bundle-extimg-pg17",
    "attestation-bundle-vex-stele_v1.2.0",
]


class BundleArtifact(unittest.TestCase):
    """bundle-artifact is one artifact name, checked before any write (#91)."""

    def assert_refused(self, run: Run) -> None:
        """Assert the bundle refusal, with nothing written at all."""
        self.assertEqual(run.code, 1, run.stderr)
        self.assertEqual(run.stdout, f"::error::{BUNDLE_MESSAGE}\n")
        self.assertEqual(run.stderr, "")
        self.assertFalse(run.output.exists())
        self.assertFalse(run.env_file.exists())
        self.assertEqual(run.files(), NO_FILES)

    def test_bundle_artifact_cannot_write_another_output(self) -> None:
        """A newline and `predicate=true` is refused and writes no output."""
        run = Run(
            self,
            {
                "UNTRUSTED_BUNDLE_ARTIFACT": "attestation-bundle\npredicate=true",
                "UNTRUSTED_SUBJECTS": manifest(1),
            },
        )
        self.assert_refused(run)

    def test_names_that_are_not_one_artifact_name(self) -> None:
        """A space, a slash, an explicit empty string, a trailing newline."""
        for name in (
            "attestation bundle",
            "attestation/bundle",
            "",
            "attestation-bundle\n",
        ):
            with self.subTest(name=name):
                self.assert_refused(
                    Run(
                        self,
                        {
                            "UNTRUSTED_BUNDLE_ARTIFACT": name,
                            "UNTRUSTED_SUBJECTS": manifest(1),
                        },
                    ),
                )

    def test_the_canon_names_pass(self) -> None:
        """Every name the canon's callers pass is written as given."""
        for name in CANON_BUNDLES:
            with self.subTest(name=name):
                run = Run(
                    self,
                    {
                        "UNTRUSTED_BUNDLE_ARTIFACT": name,
                        "UNTRUSTED_SUBJECTS": manifest(1),
                    },
                )
                self.assertEqual(run.code, 0, run.stdout + run.stderr)
                self.assertEqual(run.outputs()["bundle-artifact"], name)


class Passes(unittest.TestCase):
    """Valid requests write exactly what the attest steps read."""

    def assert_passed(self, run: Run) -> None:
        """Assert a clean exit with nothing on stderr."""
        self.assertEqual(run.code, 0, run.stdout + run.stderr)
        self.assertEqual(run.stderr, "")

    def test_openvex_predicate_with_file_subjects(self) -> None:
        """One OpenVEX predicate over file subjects."""
        predicate = (
            '{ "@context": "https://openvex.dev/ns/v0.2.0",\n  "statements": [] }'
        )
        run = Run(
            self,
            {
                "UNTRUSTED_PREDICATE_TYPE": OPENVEX,
                "UNTRUSTED_PREDICATE": predicate,
                "UNTRUSTED_SUBJECTS": manifest(2),
            },
        )
        self.assert_passed(run)
        predicate_file = run.temp / "predicate.json"
        subjects_file = run.temp / "subjects.sha256"
        outputs: dict[str, str] = {
            "bundle-artifact": "attestation-bundle",
            "predicate": "true",
            "mode": "files",
        }
        self.assertEqual(run.outputs(), outputs)
        environment: dict[str, str] = {
            "PREDICATE_FILE": str(predicate_file),
            "SUBJECTS_FILE": str(subjects_file),
        }
        self.assertEqual(run.environment(), environment)
        written = predicate_file.read_text(encoding="utf-8")
        self.assertEqual(
            written, '{"@context":"https://openvex.dev/ns/v0.2.0","statements":[]}\n'
        )
        self.assertEqual(parsed(written), parsed(predicate))
        self.assertEqual(subjects_file.read_text(encoding="utf-8"), manifest(2))
        self.assertEqual(
            run.stdout,
            f"::notice::predicate claim: {OPENVEX}\n"
            "::notice::2 subjects declared and validated\n" + manifest(2),
        )

    def test_image_subject(self) -> None:
        """An image subject writes outputs and no file."""
        run = Run(
            self,
            {
                "UNTRUSTED_IMAGE_NAME": IMAGE,
                "UNTRUSTED_IMAGE_DIGEST": DIGEST,
                "UNTRUSTED_BUNDLE_ARTIFACT": "image-bundle",
            },
        )
        self.assert_passed(run)
        outputs: dict[str, str] = {
            "bundle-artifact": "image-bundle",
            "predicate": "false",
            "mode": "image",
        }
        self.assertEqual(run.outputs(), outputs)
        self.assertEqual(run.environment(), NO_ENVIRONMENT)
        self.assertEqual(run.files(), NO_FILES)
        self.assertEqual(run.stdout, "::notice::signing one image subject\n")

    def test_1024_file_subjects(self) -> None:
        """The most an attestation carries is accepted whole."""
        run = Run(self, {"UNTRUSTED_SUBJECTS": manifest(1024)})
        self.assert_passed(run)
        subjects_file = run.temp / "subjects.sha256"
        outputs: dict[str, str] = {
            "bundle-artifact": "attestation-bundle",
            "predicate": "false",
            "mode": "files",
        }
        self.assertEqual(run.outputs(), outputs)
        environment: dict[str, str] = {"SUBJECTS_FILE": str(subjects_file)}
        self.assertEqual(run.environment(), environment)
        self.assertEqual(subjects_file.read_text(encoding="utf-8"), manifest(1024))
        self.assertEqual(
            run.stdout,
            "::notice::1024 subjects declared and validated\n" + manifest(1024),
        )

    def test_crlf_lines_and_blank_lines(self) -> None:
        """CRLF endings are dropped and blank lines are skipped, not counted."""
        subjects = "\r\n" + manifest(3, ending="\r\n") + "\r\n\n"
        run = Run(self, {"UNTRUSTED_SUBJECTS": subjects})
        self.assert_passed(run)
        self.assertEqual(
            (run.temp / "subjects.sha256").read_text(encoding="utf-8"), manifest(3)
        )
        self.assertEqual(
            run.stdout,
            "::notice::3 subjects declared and validated\n" + manifest(3),
        )


if __name__ == "__main__":
    unittest.main()
