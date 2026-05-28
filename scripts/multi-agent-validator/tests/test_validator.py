"""Tests for the multi-agent coordination plan validator."""

import subprocess
import sys
from pathlib import Path

import pytest

VALIDATOR = Path(__file__).parent.parent / "validate_multi_agent_plan.py"
EXAMPLES = Path(__file__).parent.parent / "examples"
COMPLIANT = EXAMPLES / "compliant-plan" / "aidlc-docs"


class TestCompliantPlan:
    """Tests against the example compliant plan."""

    def test_compliant_plan_passes(self):
        """A fully compliant plan should exit 0."""
        result = subprocess.run(
            [sys.executable, str(VALIDATOR), str(COMPLIANT)],
            capture_output=True, text=True
        )
        assert result.returncode == 0, f"Expected pass, got:\n{result.stdout}"
        assert "ALL CHECKS PASSED" in result.stdout

    def test_compliant_plan_json_output(self):
        """JSON output should be valid and show no blocking failures."""
        import json
        result = subprocess.run(
            [sys.executable, str(VALIDATOR), str(COMPLIANT), "--json"],
            capture_output=True, text=True
        )
        assert result.returncode == 0
        report = json.loads(result.stdout)
        assert report["summary"]["blocking_failures"] is False
        assert report["summary"]["failed"] == 0

    def test_partial_mode_skips_rules(self):
        """Partial mode should only enforce rules 01, 02, 03."""
        import json
        result = subprocess.run(
            [sys.executable, str(VALIDATOR), str(COMPLIANT), "--partial", "--json"],
            capture_output=True, text=True
        )
        assert result.returncode == 0
        report = json.loads(result.stdout)
        assert report["summary"]["skipped"] == 7  # 10 - 3

    def test_compliant_plan_detects_units(self):
        """Validator should detect units from the multi-agent plan."""
        import json
        result = subprocess.run(
            [sys.executable, str(VALIDATOR), str(COMPLIANT), "--json"],
            capture_output=True, text=True
        )
        report = json.loads(result.stdout)
        # Rule 01 should pass and mention units
        rule_01 = next(f for f in report["findings"] if f["rule_id"] == "MULTI-AGENT-01")
        assert rule_01["status"] == "PASS"


class TestEmptyProject:
    """Tests against an empty directory (should fail or N/A)."""

    def test_empty_dir_has_failures(self, tmp_path):
        """An empty aidlc-docs should produce failures."""
        result = subprocess.run(
            [sys.executable, str(VALIDATOR), str(tmp_path)],
            capture_output=True, text=True
        )
        assert result.returncode == 1  # blocking failures
        assert "BLOCKING FINDINGS" in result.stdout

    def test_empty_dir_json(self, tmp_path):
        """Empty dir in JSON mode should report failures."""
        import json
        result = subprocess.run(
            [sys.executable, str(VALIDATOR), str(tmp_path), "--json"],
            capture_output=True, text=True
        )
        assert result.returncode == 1
        report = json.loads(result.stdout)
        assert report["summary"]["blocking_failures"] is True


class TestNonexistentPath:
    """Tests for invalid paths."""

    def test_missing_path_exits_2(self):
        """Non-existent path should exit with code 2."""
        result = subprocess.run(
            [sys.executable, str(VALIDATOR), "/nonexistent/path"],
            capture_output=True, text=True
        )
        assert result.returncode == 2


class TestStrictMode:
    """Tests for strict mode (N/A treated as failures)."""

    def test_strict_mode_fails_on_na(self, tmp_path):
        """Strict mode should treat N/A as failures."""
        import json
        # Create minimal plan with some content but no units
        (tmp_path / "multi-agent-plan.md").write_text(
            "# Plan\n## Cost-Benefit\nparallelizable fraction p = 0.8\n"
            "## Dependency Graph\ndepends on: nothing\n"
            "## Isolation\nfeature branch per agent\n"
            "## Communication Protocol\ncentralized orchestrator\n"
            "## Rollback\nrollback: revert branch\n"
            "## Shared State\nno shared mutable state\n"
            "## Integration\nintegration verification after merge\n"
        )
        result = subprocess.run(
            [sys.executable, str(VALIDATOR), str(tmp_path), "--strict", "--json"],
            capture_output=True, text=True
        )
        report = json.loads(result.stdout)
        # Some rules should be N/A (no units) → strict makes them FAIL
        na_rules = [f for f in report["findings"] if "strict mode" in f.get("message", "")]
        assert len(na_rules) > 0
