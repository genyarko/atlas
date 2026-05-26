"""IntelligenceModule contract.

Every module:
1. Has a stable name (matches the ``ModuleName`` literal in ``models``)
2. Lists the Bright Data tools it depends on (used in the brief footer)
3. Implements ``execute(params)`` returning a ``ModuleResult``
4. Implements ``mock(params)`` returning hardcoded fixture data so the
   foundation pipeline works end-to-end with zero credentials

The executor chooses between ``execute`` and ``mock`` based on the
global ``ATLAS_MODE`` setting and the module's own readiness flag.
"""

from __future__ import annotations

import asyncio
import time
from abc import ABC, abstractmethod
from typing import Any

from .. import config
from ..models import ModuleName, ModuleResult


# Static catalog used by the Planner prompt and by the brief renderer.
MODULE_CATALOG: dict[str, dict[str, Any]] = {
    "trueprice": {
        "title": "TruePrice",
        "track": "GTM",
        "purpose": "True purchase cost via checkout completion across geographies.",
        "bright_data_tools": ["scraping_browser", "residential_proxies"],
    },
    "signal": {
        "title": "Signal",
        "track": "GTM + Finance",
        "purpose": "Strategic intent inferred from hiring, exec moves, tech stack.",
        "bright_data_tools": ["web_scraper_api", "serp_api"],
    },
    "filing": {
        "title": "Filing",
        "track": "Finance",
        "purpose": "Materiality-scored diffs of SEC, regulatory, and patent filings.",
        "bright_data_tools": ["web_unlocker"],
    },
    "altdata": {
        "title": "AltData",
        "track": "Finance",
        "purpose": "Composite distress/momentum from reviews and alt-data signals.",
        "bright_data_tools": ["web_scraper_api"],
    },
    "visual": {
        "title": "Visual",
        "track": "Security",
        "purpose": "Brand-impersonation detection via vision-diff of suspect domains.",
        "bright_data_tools": ["serp_api", "scraping_browser"],
    },
    "exposure": {
        "title": "Exposure",
        "track": "Security",
        "purpose": "Credentials, PII, and doxx surface across the open web.",
        "bright_data_tools": ["serp_api", "web_unlocker"],
    },
    "investor": {
        "title": "Investor",
        "track": "GTM + Finance",
        "purpose": "Active VC firms and partners investing in a target sector — fundable contacts and portfolio signals.",
        "bright_data_tools": ["web_scraper_api", "serp_api"],
    },
}


class IntelligenceModule(ABC):
    """Base class for all six modules."""

    #: The literal module name (key in ``MODULES``).
    name: ModuleName

    #: Whether the live ``execute`` path is implemented yet. Default False —
    #: each module flips this to True as it gets promoted in Days 3+.
    live_ready: bool = False

    @abstractmethod
    async def mock(self, params: dict[str, Any]) -> ModuleResult:
        """Return a deterministic fixture. Always available."""

    async def execute(self, params: dict[str, Any]) -> ModuleResult:
        """Default implementation: delegate to mock until the module is promoted."""
        return await self.mock(params)

    async def run(self, params: dict[str, Any]) -> ModuleResult:
        """Public entry point used by the Executor.

        Picks the live path only if (a) global mode is live, (b) credentials
        are present, and (c) the module has self-reported readiness. Falls
        back to mock on any exception so a flaky live call can never crash
        the pipeline mid-demo.
        """
        start = time.perf_counter()
        try:
            use_live = (
                not config.is_mock_mode()
                and config.has_brightdata_creds()
                and self.live_ready
            )
            if use_live:
                result = await self.execute(params)
            else:
                result = await self.mock(params)
        except (asyncio.CancelledError, KeyboardInterrupt):
            raise
        except Exception as exc:  # pragma: no cover — defensive
            # Live path threw; fall back to mock so the brief still renders.
            result = await self.mock(params)
            result.status = "partial"
            result.error = f"{type(exc).__name__}: {exc}"
        result.duration_ms = int((time.perf_counter() - start) * 1000)
        return result
