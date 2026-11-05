import os
import shutil
import tempfile
from unittest.mock import patch

from core import config
from core.db import init_db


class TempDBMixin:
    """Un database SQLite nuovo per ogni test (DB_PATH, DATA_DIR e TMP_DIR puntano a una cartella temporanea)."""

    def setUp(self):
        super().setUp()
        self.temp_dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.temp_dir, ignore_errors=True)
        tmp_dir = os.path.join(self.temp_dir, "tmp")
        os.makedirs(tmp_dir)
        for name, value in (("DB_PATH", os.path.join(self.temp_dir, "articles.db")), ("DATA_DIR", self.temp_dir),
                            ("TMP_DIR", tmp_dir)):
            patcher = patch.object(config, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        init_db()
