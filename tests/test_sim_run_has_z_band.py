from __future__ import annotations

from sim import run as sim_run


def test_build_parser_exposes_z_band_mm_flag() -> None:
    parser = sim_run.build_parser()
    action = next(
        (a for a in parser._actions if "--z-band-mm" in getattr(a, "option_strings", ())),  # noqa: SLF001
        None,
    )
    assert action is not None
    assert str(action.dest) == "z_band_mm"
