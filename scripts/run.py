"""Start a project module independently of launchd's inherited directory."""
from pathlib import Path
import os
import platform
import runpy
import sys

root = Path(__file__).resolve().parent.parent
os.chdir(root)
sys.path.insert(0, str(root))
module = sys.argv.pop(1)
if module == "parking_watch.service" and platform.machine() == "x86_64":
    # The pinned Intel Ultralytics build applies this value in select_device.
    # Reserve CPU capacity for camera decoding on older dual-core Macs.
    import ultralytics.utils.torch_utils as torch_utils
    torch_utils.NUM_THREADS = max(1, (os.cpu_count() or 2) // 4)
runpy.run_module(module, run_name="__main__")
