import contextlib
import io
import json
import sqlite3
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import fetch_gics


class GicsDateTests(unittest.TestCase):
    def collect(self, now):
        dates = []

        def fetch_group(session, code, dt):
            dates.append(dt)
            return [{"isu_cd": "005930", "isu_abbr": "삼성전자"}] if code == "4530" else []

        with tempfile.TemporaryDirectory() as directory:
            db = Path(directory) / "market.sqlite"
            with contextlib.closing(sqlite3.connect(db)) as con:
                con.executescript("CREATE TABLE stock(code TEXT PRIMARY KEY, is_common INT); CREATE TABLE meta(k TEXT PRIMARY KEY, v TEXT);"
                                  "INSERT INTO stock VALUES('005930', 1);")
            with patch.object(fetch_gics, "DB", db), patch.object(fetch_gics, "fetch_group", fetch_group), \
                 patch.object(fetch_gics.time, "sleep"), patch("market_dates.korea_now", return_value=now), \
                 contextlib.redirect_stdout(io.StringIO()):
                fetch_gics.main()
            with contextlib.closing(sqlite3.connect(db)) as con:
                meta = dict(con.execute("SELECT k, v FROM meta"))
                gics = con.execute("SELECT gics_ig FROM stock WHERE code='005930'").fetchone()[0]
        return set(dates), meta, gics

    def test_morning_run_queries_previous_completed_session(self):
        # 2026-10-07 08:10 정기실행: 당일(20261007) 조회는 KRX가 빈 목록을 돌려준다.
        dates, meta, gics = self.collect(datetime(2026, 10, 7, 8, 10))
        self.assertEqual(dates, {"20261006"})
        self.assertEqual(meta["gics_date"], "20261006")
        self.assertEqual(json.loads(meta["gics_collection"])["status"], "ok")
        self.assertEqual(gics, "4530")

    def test_after_close_queries_same_day(self):
        dates, meta, _ = self.collect(datetime(2026, 10, 7, 16, 0))
        self.assertEqual(dates, {"20261007"})
        self.assertEqual(meta["gics_date"], "20261007")


if __name__ == "__main__":
    unittest.main()
