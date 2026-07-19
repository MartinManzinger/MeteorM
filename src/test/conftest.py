"""Test setup for the flat source directory."""

import os
import sys
from pathlib import Path


SOURCE_DIRECTORY = Path(__file__).resolve().parent.parent
if str(SOURCE_DIRECTORY) not in sys.path:
    sys.path.insert(0, str(SOURCE_DIRECTORY))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
