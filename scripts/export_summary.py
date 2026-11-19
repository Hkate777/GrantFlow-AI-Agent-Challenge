"""薄封裝：`python scripts/export_summary.py` 生成評審彙總 Excel。

README 以此命令為準（`python -m kkg.export` 在 src 佈局下不可直接運行）。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from kkg.export import main  # noqa: E402

if __name__ == "__main__":
    main()
