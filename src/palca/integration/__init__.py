from __future__ import annotations

from typing import Any

__all__ = ["PolicyConfig", "PolicyPackerScheduler", "SUPPORTED_LOOKAHEAD_K"]


def __getattr__(name: str) -> Any:
    if name in __all__:
        from .policy_packer_sched import PolicyConfig, PolicyPackerScheduler, SUPPORTED_LOOKAHEAD_K

        exports = {
            "PolicyConfig": PolicyConfig,
            "PolicyPackerScheduler": PolicyPackerScheduler,
            "SUPPORTED_LOOKAHEAD_K": SUPPORTED_LOOKAHEAD_K,
        }
        return exports[name]
    raise AttributeError(name)
