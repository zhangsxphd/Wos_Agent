"""Run the complete suite with Python socket connections disabled."""

import socket
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

if __name__ == "__main__":
    suite = unittest.defaultTestLoader.discover(str(ROOT / "tests"))
    with patch.object(socket.socket, "connect", side_effect=AssertionError("Network forbidden in tests")), \
         patch.object(socket.socket, "connect_ex", side_effect=AssertionError("Network forbidden in tests")), \
         patch.object(socket, "create_connection", side_effect=AssertionError("Network forbidden in tests")):
        result = unittest.TextTestRunner(verbosity=2 if "-v" in sys.argv else 1).run(suite)
    raise SystemExit(0 if result.wasSuccessful() else 1)
