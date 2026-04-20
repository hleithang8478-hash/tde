# -*- coding: utf-8 -*-
"""在仓库根执行: python ems_self_check.py（转发到 scripts/ems_self_check.py）"""
import runpy
import sys
from pathlib import Path

root = Path(__file__).resolve().parent
script = root / "scripts" / "ems_self_check.py"
if not script.is_file():
    print(f"找不到: {script}", file=sys.stderr)
    sys.exit(2)
sys.argv[0] = str(script)
runpy.run_path(str(script), run_name="__main__")
