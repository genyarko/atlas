"""Intelligence modules — discovered and dispatched by the Executor node."""

from .altdata import AltDataModule
from .base import IntelligenceModule, MODULE_CATALOG
from .exposure import ExposureModule
from .filing import FilingModule
from .investor import InvestorModule
from .signal import SignalModule
from .trueprice import TruePriceModule
from .visual import VisualModule

# Order is deterministic — matches Bright Data integration footprint
# in the implementation plan.
MODULES: dict[str, IntelligenceModule] = {
    "trueprice": TruePriceModule(),
    "signal": SignalModule(),
    "filing": FilingModule(),
    "altdata": AltDataModule(),
    "visual": VisualModule(),
    "exposure": ExposureModule(),
    "investor": InvestorModule(),
}

__all__ = ["MODULES", "MODULE_CATALOG", "IntelligenceModule"]
