"""Check the public project VERSION without modifying the local installation."""
import time
from pathlib import Path
from urllib.request import Request, urlopen

VERSION_URL = "https://raw.githubusercontent.com/David0936/claworld-fomo/main/VERSION"
PROJECT_URL = "https://github.com/David0936/claworld-fomo"


def parse_version(value):
    parts = value.strip().split(".")
    if len(parts) != 3 or any(not part.isdigit() for part in parts):
        raise ValueError("版本号格式无效")
    return tuple(int(part) for part in parts)


def local_version(root):
    return (Path(root) / "VERSION").read_text(encoding="utf-8").strip()


def check(root, opener=urlopen, now=time.time):
    current = local_version(root)
    request = Request(VERSION_URL, headers={"User-Agent": "Claworld-Fomo-Update-Checker"})
    with opener(request, timeout=8) as response:
        latest = response.read(64).decode("utf-8").strip()
    result = {
        "status": "available" if parse_version(latest) > parse_version(current) else "current",
        "current": current,
        "latest": latest,
        "checked_at": now(),
        "url": PROJECT_URL,
    }
    return result

