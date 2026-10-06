"""Run both module suites and repeat their offline examples in fresh directories.

No live collection command is called. A supplied output path must not already
exist; without --output, generated files are removed with a temporary directory.
Only stable counts and content hashes enter the report, not test durations.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile


ROOT = Path(__file__).resolve().parent
MODULES = (("market-data", "market_archive"), ("physical-data", "physical_data"))


def run_python(arguments: list[str], cwd: Path) -> subprocess.CompletedProcess:
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", PYTHONPATH="")
    completed = subprocess.run(
        [sys.executable, *arguments], cwd=cwd, env=env,
        capture_output=True, text=True, check=False,
    )
    if completed.returncode:
        raise RuntimeError(
            f"{cwd.name}: python {' '.join(arguments)} failed\n"
            f"{completed.stdout}{completed.stderr}"
        )
    return completed


def file_hashes(root: Path) -> dict[str, str]:
    files = {}
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise RuntimeError("Example output must not contain symbolic links")
        if path.is_file():
            files[path.relative_to(root).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    if not files:
        raise RuntimeError(f"No example output was generated for {root.name}")
    return files


def verify(output_root: Path) -> dict:
    output_root.mkdir(parents=True, exist_ok=False)
    report = {"mode": "offline_fixture_verification", "python": sys.version.split()[0], "modules": {}}
    with tempfile.TemporaryDirectory(prefix="portfolio-repeat-") as second_root:
        for folder, module in MODULES:
            module_root = ROOT / folder
            tested = run_python(["-m", "unittest", "discover", "-s", "tests", "-v"], module_root)
            matched = re.search(r"Ran (\d+) tests?", tested.stdout + tested.stderr)
            if not matched or int(matched.group(1)) == 0:
                raise RuntimeError(f"No tests were discovered for {folder}")
            first = output_root / folder
            second = Path(second_root) / folder
            run_python(["-m", module, "demo", "--output", str(first)], module_root)
            run_python(["-m", module, "demo", "--output", str(second)], module_root)
            first_hashes, second_hashes = file_hashes(first), file_hashes(second)
            if first_hashes != second_hashes:
                changed = sorted(set(first_hashes) | set(second_hashes))
                changed = [name for name in changed if first_hashes.get(name) != second_hashes.get(name)]
                raise RuntimeError(f"Nondeterministic output in {folder}: {changed}")
            if module == "market_archive":
                run_python(["-m", module, "verify", "--root", str(first)], module_root)
            report["modules"][folder] = {
                "tests_passed": int(matched.group(1)),
                "repeat_output_identical": True,
                "output_files": first_hashes,
            }
    report["tests_passed"] = sum(item["tests_passed"] for item in report["modules"].values())
    (output_root / "verification.json").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, help="Keep outputs in a new directory that does not already exist")
    args = parser.parse_args()
    if args.output is None:
        with tempfile.TemporaryDirectory(prefix="portfolio-verify-") as temp:
            report = verify(Path(temp) / "results")
    else:
        destination = args.output.expanduser().resolve()
        if destination.exists():
            parser.error("--output must name a new directory; existing files will not be overwritten")
        report = verify(destination)
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
