"""验证假课题与存档两个功能。"""

import shutil
import sys
from pathlib import Path

APP = Path(__file__).resolve().parent
sys.path.insert(0, str(APP))

import backup  # noqa: E402
import fake  # noqa: E402

REPO = APP.parents[2]
charts = REPO / "charts"
source = charts / "DistortedFate.Sakuzyo.IN.json"

print("=== 假课题 ===")
for mode in ("Mirror", "Shuffle", "Shift"):
    target = fake.make_fake(source, charts, mode)
    print(f"  {mode:8} -> {target.name}  {target.stat().st_size // 1024} KB")
    target.unlink()

print("=== 存档 ===")
folder = backup.create_backup()
print("  备份到:", folder)
print("  文件:", [p.name for p in folder.iterdir()])
print("  备份列表:", [p.name for p in backup.list_backups()][:3])
restored = backup.restore_backup(folder)
print("  恢复:", restored)
shutil.rmtree(folder)
print("  已清理测试备份")
