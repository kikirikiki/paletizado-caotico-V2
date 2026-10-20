"""D-015 diagnostic replay using the accessibility rule of each execution."""
from .validate import validate_accessibility


def accessibility_gap_violations(items: list[dict], delta_mm: int, gap_mm: int,
                                 tall_box_exemption: bool = False, top_clearance_mm: int = 0) -> int:
    return len(validate_accessibility(items, dict(
        accessibility_delta_mm=delta_mm,
        accessibility_tall_box_exemption=tall_box_exemption,
        accessibility_top_clearance_mm=top_clearance_mm,
    ), gap_mm=gap_mm))
