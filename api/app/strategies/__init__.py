from app.strategies.library import STRATEGIES, build_signal
from app.strategies.presets import PRESET_STRATEGIES

# Video presets are DSL-defined strategies; they appear in the same registry so they work
# everywhere a built-in strategy does (picker, compare, risk engine).
STRATEGIES.update(PRESET_STRATEGIES)

__all__ = ["STRATEGIES", "build_signal"]
