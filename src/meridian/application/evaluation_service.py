"""Framework-neutral access to the evaluation report and harness."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from .models import EvaluationReport, EvaluationRunResult

PROJECT_ROOT = Path(__file__).resolve().parents[3]
_PACKAGED_EVAL_DIR = Path(__file__).resolve().parents[1] / "_evals"
EVAL_TIMEOUT_SECONDS = 900
TIMEOUT_RETURN_CODE = 124
STDOUT_LIMIT = 4000
STDERR_LIMIT = 2000


_PACKAGED_EVALS_AVAILABLE = (_PACKAGED_EVAL_DIR / "run_evals.py").exists()
_EVAL_DIR = _PACKAGED_EVAL_DIR if _PACKAGED_EVALS_AVAILABLE else PROJECT_ROOT / "evals"
DEFAULT_REPORT_PATH = _EVAL_DIR / "report.md"
DEFAULT_EVAL_SCRIPT = _EVAL_DIR / "run_evals.py"


def _text(output: str | bytes | None) -> str:
    if output is None:
        return ""
    if isinstance(output, bytes):
        return output.decode("utf-8", errors="replace")
    return output


def load_evaluation_report(path: Path | None = None) -> EvaluationReport:
    report_path = path or DEFAULT_REPORT_PATH
    if not report_path.exists():
        return EvaluationReport(
            exists=False,
            markdown="No evaluation report exists yet.",
            path=str(report_path),
        )
    return EvaluationReport(
        exists=True,
        markdown=report_path.read_text(encoding="utf-8"),
        path=str(report_path),
        modified_at=report_path.stat().st_mtime,
    )


def run_evaluations(
    *,
    script: Path | None = None,
    report_path: Path | None = None,
    cwd: Path | None = None,
) -> EvaluationRunResult:
    eval_script = script or DEFAULT_EVAL_SCRIPT
    try:
        process = subprocess.run(
            [sys.executable, str(eval_script)],
            capture_output=True,
            text=True,
            cwd=str(cwd or PROJECT_ROOT),
            check=False,
            timeout=EVAL_TIMEOUT_SECONDS,
        )
        returncode = process.returncode
        stdout = process.stdout or ""
        stderr = process.stderr or ""
    except subprocess.TimeoutExpired as exc:
        returncode = TIMEOUT_RETURN_CODE
        stdout = _text(exc.stdout)
        stderr = _text(exc.stderr)
        stderr += f"\nEvaluation timed out after {EVAL_TIMEOUT_SECONDS} seconds."
    output = stdout[-STDOUT_LIMIT:]
    if stderr:
        output += "\n" + stderr[-STDERR_LIMIT:]
    return EvaluationRunResult(
        returncode=returncode,
        output=output,
        report=load_evaluation_report(report_path),
    )
