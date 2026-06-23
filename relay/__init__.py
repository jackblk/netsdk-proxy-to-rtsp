"""NetSDK -> RTSP proxy package."""
import sys
from pathlib import Path

# Project root holds the vendored `NetSDK` package (relative-import namespace package).
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))
