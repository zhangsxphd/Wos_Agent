"""Run portable regressions or explicit historical audits without network."""

import argparse
import json
import socket
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from suite_profiles import select_tests, missing_historical_artifacts


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("-v", "--verbose", action="store_true")
    parser.add_argument("--suite", choices=("portable", "historical", "all"), default="portable")
    parser.add_argument("--without-xlsx", action="store_true", help="Explicitly omit the three real XLSX integration tests")
    parser.add_argument("--report", type=Path, help="Save executed/excluded case IDs and results as JSON")
    args = parser.parse_args()
    discovered = unittest.defaultTestLoader.discover(str(ROOT / "tests"))
    suite, excluded = select_tests(discovered, args.suite, args.without_xlsx)
    summary = {"suite": args.suite, "selected": suite.countTestCases(), "excluded": excluded}
    missing = missing_historical_artifacts(suite) if args.suite != "portable" else []
    if missing:
        summary.update(status="PREREQUISITES_MISSING", missing_artifacts=missing)
        print(json.dumps(summary, ensure_ascii=False, indent=2), file=sys.stderr)
        if args.report:
            args.report.parent.mkdir(parents=True, exist_ok=True)
            args.report.write_text(json.dumps(summary, indent=2) + "\n")
        return 2
    print(f"Suite={args.suite}: {summary['selected']} selected; {len(excluded)} explicitly excluded", flush=True)
    with patch.object(socket.socket, "connect", side_effect=AssertionError("Network forbidden in tests")), \
         patch.object(socket.socket, "connect_ex", side_effect=AssertionError("Network forbidden in tests")), \
         patch.object(socket, "create_connection", side_effect=AssertionError("Network forbidden in tests")):
        result = unittest.TextTestRunner(verbosity=2 if args.verbose else 1).run(suite)
    summary.update(status="PASS" if result.wasSuccessful() else "FAIL", run=result.testsRun,
                   failures=[case.id() for case, _ in result.failures], errors=[case.id() for case, _ in result.errors],
                   skipped=[{"test": case.id(), "reason": reason} for case, reason in result.skipped])
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n")
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
