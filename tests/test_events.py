import json
from pathlib import Path
import tempfile
import unittest

from fyk_agent.events import EventLog


class EventLogTests(unittest.TestCase):
    def test_unpaired_unicode_surrogate_cannot_crash_logging(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            log = EventLog(Path(directory))
            log.emit("test", text="bad surrogate: \udcaa")
            raw = log.path.read_text(encoding="utf-8")
            record = json.loads(raw)
            self.assertEqual(record["kind"], "test")
            self.assertIn("bad surrogate", record["text"])


if __name__ == "__main__":
    unittest.main()
