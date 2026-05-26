"""Per-module smoke tests — every stub must return a valid ModuleResult."""

from __future__ import annotations

import pytest

from app.models import MODULE_NAMES
from app.modules import MODULES


@pytest.mark.parametrize("name", MODULE_NAMES)
async def test_module_mock_returns_valid_result(name):
    module = MODULES[name]
    result = await module.mock({"query": "test on AcmeCorp", "subject": "AcmeCorp"})
    assert result.module == name
    assert result.status in ("success", "partial", "failed")
    assert 0.0 <= result.confidence <= 1.0
    # Each stub should contribute at least one finding so the brief isn't empty.
    assert result.findings, f"module {name} produced no findings"
    for finding in result.findings:
        assert finding.statement
        assert finding.severity in ("info", "notable", "high", "critical")


async def test_module_run_sets_duration():
    result = await MODULES["signal"].run({"query": "Run Signal on Linear", "subject": "Linear"})
    assert result.duration_ms >= 0
