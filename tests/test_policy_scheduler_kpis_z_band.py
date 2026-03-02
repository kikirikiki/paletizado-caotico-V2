from __future__ import annotations

from palca.integration.policy_packer_sched import PolicyPackerScheduler


def test_collect_scheduler_kpis_uses_greedy_z_band_when_micro_is_only_fallback_metadata() -> None:
    policy = PolicyPackerScheduler.from_defaults()
    policy._scheduler.last_eval_stats = {
        "mode": "greedy",
        "z_band_enabled": True,
        "z_band_mm": 0,
        "z_band_removed": 3,
        "micro_plan": {
            "z_band_enabled": True,
            "z_band_mm": 0,
            "z_band_removed": 0,
        },
    }

    kpis = policy.collect_kpis()
    assert bool(kpis.get("z_band_enabled")) is True
    assert int(kpis.get("z_band_mm", -1)) == 0
    assert int(kpis.get("z_band_removed", -1)) == 3
