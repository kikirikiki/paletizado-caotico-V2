from __future__ import annotations

from sim import run as sim_run


def test_parser_includes_z_band_mm_option() -> None:
    parser = sim_run.build_parser()
    action = next((item for item in parser._actions if "--z-band-mm" in item.option_strings), None)  # noqa: SLF001
    assert action is not None
    assert action.dest == "z_band_mm"
