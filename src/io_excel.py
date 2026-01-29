from __future__ import annotations

from pathlib import Path

import pandas as pd


def load_boxes(excel_path: str | Path) -> pd.DataFrame:
    path = Path(excel_path)
    if not path.exists():
        raise FileNotFoundError(f"Excel no encontrado: {path}")
    return pd.read_excel(path)
