from datetime import datetime

from .presentation import LOCAL_ZONE
from .runtime import ROOT
from .retention import data_lock


def archive_photo(photo, stamp, kind):
    folder = ROOT / "data/telegram-photos"
    name = datetime.fromtimestamp(stamp, LOCAL_ZONE).strftime("%Y%m%dT%H%M%S.%f%z")
    path = folder / f"{name}-{kind}.jpg"
    with data_lock(ROOT):
        folder.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_bytes(photo)
        temporary.replace(path)
    return str(path.relative_to(ROOT))
