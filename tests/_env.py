import shutil
import tempfile
import unittest
from pathlib import Path

import config
from db.schema import init_all


class EnvTest(unittest.TestCase):
    """هر تست روی یک کپی تازه از دیتابیس پایه و یک دیتابیس خروجی خالی اجرا می‌شود."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self._orig = (config.BASE_DB, config.OUTPUT_DB)
        shutil.copy(self._orig[0], self.tmp / "university.db")
        config.BASE_DB, config.OUTPUT_DB = self.tmp / "university.db", self.tmp / "out.db"
        init_all()

    def tearDown(self):
        config.BASE_DB, config.OUTPUT_DB = self._orig
        shutil.rmtree(self.tmp, ignore_errors=True)
