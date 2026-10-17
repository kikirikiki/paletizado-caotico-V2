"""D-015 diagnostic replay; does not change the KPI or production rule."""


def accessibility_gap_violations(items: list[dict], delta_mm: int, gap_mm: int) -> int:
    if delta_mm <= 0:
        return 0
    previous = []
    violations = 0
    for item in items:
        x, y, z = (item[k] for k in ('x_mm', 'y_mm', 'z_mm'))
        l, w, h = (item[k] for k in ('length_mm', 'width_mm', 'height_mm'))
        local_z = max((pz+ph for px,py,pz,pl,pw,ph in previous
                       if min(px+pl,x+l) > max(px,x) and min(py+pw,y+w) > max(py,y)), default=0)
        violations += int(any(
            max(px,x)-min(px+pl,x+l) <= gap_mm
            and max(py,y)-min(py+pw,y+w) <= gap_mm
            and pz+ph > local_z+delta_mm
            for px,py,pz,pl,pw,ph in previous))
        previous.append((x,y,z,l,w,h))
    return violations
