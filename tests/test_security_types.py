import copy
import io
import json
import sqlite3
import tempfile
import unittest
import zipfile
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from fetch_market import is_common, store_universe
from security_types import (FIELD_WIDTHS, MASTER_URLS, PREFERRED_FIELD, SCHEMA_URLS,
                            common_decision, metadata_quality, parse_master_zip,
                            refresh_metadata, validate_metadata)


def record(code, name, market="KOSDAQ", preferred="0", group="ST"):
    return dict(code=code, name=name, market=market, isin="KR7000000000",
                security_group=group, preferred_code=preferred)


def payload(records=None):
    return dict(schema_version=1, observed_at="2026-10-26 12:00", sources={
        m: dict(url=url, schema_url=SCHEMA_URLS[m], sha256="a" * 64) for m, url in MASTER_URLS.items()},
        records=records or [record("005930", "삼성전자", "KOSPI"), record("458650", "성우")])


def master(market, rows):
    raw = []
    for row in rows:
        head = row["code"].encode("ascii").ljust(9) + row["isin"].encode("ascii") + row["name"].encode("cp949").ljust(40)
        tail = bytearray(b"0" * sum(FIELD_WIDTHS[market]))
        tail[:2] = row["security_group"].encode("ascii")
        offset = sum(FIELD_WIDTHS[market][:PREFERRED_FIELD[market]])
        tail[offset:offset + 1] = (row["preferred_code"] or " ").encode("ascii")
        raw.append(head + tail)
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        archive.writestr(market.lower() + "_code.mst", b"\n".join(raw) + b"\n")
    return stream.getvalue()


class SecurityIdentityTests(unittest.TestCase):
    def test_common_names_ending_woo_and_alphanumeric_code_use_explicit_type(self):
        records = [record("458650", "성우"), record("159910", "에코글로우"),
                   record("294090", "이오플로우"), record("0009K0", "문자코드"),
                   record("123455", "코드끝도추론안함")]
        lookup = {r["code"]: r for r in records}
        for row in records:
            self.assertIs(is_common(row["code"], row["name"], row["market"], lookup), True)
        self.assertIsNone(is_common("458650", "성우"))

    def test_preferred_counterexamples_include_b_and_digit_nine(self):
        rows = [record("005935", "삼성전자우", "KOSPI", "1"),
                record("00088K", "한화3우B", "KOSPI", "2"),
                record("03473K", "SK우", "KOSPI", "9"),
                record("123450", "이름만보통주", preferred="1")]
        lookup = {r["code"]: r for r in rows}
        for row in rows:
            self.assertIs(common_decision(row["code"], row["name"], row["market"], lookup), False)

    def test_non_stock_types_and_identity_mismatch(self):
        for group in ("RT", "PF", "FS", "DR", "EF", "EN", "BC"):
            row = record("123450", "종목", group=group)
            self.assertIs(common_decision("123450", "종목", "KOSDAQ", {"123450": row}), False)
        row = record("458650", "성우")
        self.assertIsNone(common_decision("458650", "다른회사", "KOSDAQ", {"458650": row}))
        self.assertIsNone(common_decision("458650", "성우", "KOSPI", {"458650": row}))
        self.assertIsNone(common_decision("458650", "성우", "KOSDAQ", {}))

    def test_invalid_duplicate_missing_and_type_metadata_fail_closed(self):
        for change in ("duplicate", "market", "source", "date", "missing_type", "integer_preferred"):
            data = payload()
            if change == "duplicate": data["records"].append(copy.deepcopy(data["records"][0]))
            if change == "market": data["records"] = data["records"][:1]
            if change == "source": data["sources"]["KOSDAQ"]["url"] = "https://invalid.example/master"
            if change == "date": data["observed_at"] = "unavailable"
            if change == "missing_type": del data["records"][0]["security_group"]
            if change == "integer_preferred": data["records"][0]["preferred_code"] = 0
            with self.subTest(change=change), self.assertRaises((ValueError, TypeError)):
                validate_metadata(data)


class MasterParserTests(unittest.TestCase):
    def test_schema_widths_and_both_markets_parse_utf8_names(self):
        self.assertEqual(sum(FIELD_WIDTHS["KOSPI"]), 227)
        self.assertEqual(sum(FIELD_WIDTHS["KOSDAQ"]), 221)
        for market in MASTER_URLS:
            rows = [record("005930", "삼성전자", market), record("005935", "삼성전자우", market, "1")]
            self.assertEqual(parse_master_zip(master(market, rows), market), rows)

    def test_non_stock_seven_and_nine_character_identifiers_are_not_bad_master(self):
        rows = [record("F70100034", "비상장펀드", "KOSPI", group="BC"),
                record("Q500067", "ETN", "KOSPI", group="EN")]
        self.assertEqual(parse_master_zip(master("KOSPI", rows), "KOSPI"), rows)

    def test_any_malformed_line_or_duplicate_rejects_the_entire_file(self):
        row = record("458650", "성우")
        blob = master("KOSDAQ", [row])
        with zipfile.ZipFile(io.BytesIO(blob)) as archive:
            raw = archive.read("kosdaq_code.mst")
        for invalid in (raw[:-2] + b"\n", raw + raw, raw + b"\n"):
            stream = io.BytesIO()
            with zipfile.ZipFile(stream, "w") as archive: archive.writestr("kosdaq_code.mst", invalid)
            with self.assertRaises(ValueError): parse_master_zip(stream.getvalue(), "KOSDAQ")

    def test_refresh_gets_exactly_two_public_files_and_validates_before_replace(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "security_types.json"
            replies = [SimpleNamespace(content=master(m, [record("005930" if m == "KOSPI" else "458650", "삼성전자" if m == "KOSPI" else "성우", m)]), raise_for_status=lambda: None) for m in MASTER_URLS]
            get = Mock(side_effect=replies)
            lookup, quality = refresh_metadata(path, "2026-10-26 12:00", get)
            self.assertEqual(quality["status"], "ok")
            self.assertEqual(len(lookup), 2)
            self.assertEqual(get.call_count, 2)
            self.assertEqual([call.args[0] for call in get.call_args_list], list(MASTER_URLS.values()))
            self.assertTrue(all(call.kwargs == {"timeout": 30} for call in get.call_args_list))
            self.assertEqual(len(validate_metadata(json.loads(path.read_text(encoding="utf-8")))), 2)
            before = path.read_bytes()
            fallback, quality = refresh_metadata(path, "2026-10-27 12:00", Mock(side_effect=OSError("private/path")))
            self.assertEqual(path.read_bytes(), before)
            self.assertEqual(fallback, lookup)
            self.assertEqual(quality["status"], "fallback")
            self.assertNotIn("private/path", json.dumps(quality))

    def test_partial_or_invalid_refresh_never_erases_a_valid_cache(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "security_types.json"
            path.write_text(json.dumps(payload(), ensure_ascii=False), encoding="utf-8")
            before = path.read_bytes()
            get = Mock(side_effect=[SimpleNamespace(content=master("KOSPI", [record("005930", "삼성전자", "KOSPI")]), raise_for_status=lambda: None), RuntimeError("failed second market")])
            lookup, quality = refresh_metadata(path, "2026-10-27 12:00", get)
            self.assertEqual(len(lookup), 2)
            self.assertEqual(quality["status"], "fallback")
            self.assertEqual(path.read_bytes(), before)

    def test_unavailable_cache_returns_no_invented_types(self):
        with tempfile.TemporaryDirectory() as folder:
            lookup, quality = refresh_metadata(Path(folder) / "none.json", "2026-10-26 12:00", Mock(side_effect=RuntimeError()))
            self.assertEqual(lookup, {})
            self.assertEqual(quality["status"], "unavailable")


class StoreFallbackTests(unittest.TestCase):
    def setUp(self):
        self.con = sqlite3.connect(":memory:")
        self.con.executescript("CREATE TABLE stock(code TEXT PRIMARY KEY,name TEXT,market TEXT,is_common INT,close REAL,mktcap REAL,trdval REAL,volume REAL,updated TEXT); CREATE TABLE meta(k TEXT PRIMARY KEY,v TEXT);")
        self.rows = [dict(code="458650", name="성우", market="KOSDAQ", close=100, mktcap=1e8, trdval=1e7, volume=100),
                     dict(code="005935", name="삼성전자우", market="KOSPI", close=100, mktcap=1e8, trdval=1e7, volume=100)]

    def tearDown(self): self.con.close()

    def test_verified_identity_fixes_wrong_name_suffix_and_excludes_preferred(self):
        lookup = {r["code"]: r for r in [record("458650", "성우"), record("005935", "삼성전자우", "KOSPI", "1")]}
        store_universe(self.con, self.rows, "2026-10-26 12:00", lookup, dict(status="ok", warnings=[]))
        self.assertEqual(dict(self.con.execute("SELECT code,is_common FROM stock")), {"458650": 1, "005935": 0})

    def test_missing_or_conflicting_metadata_preserves_existing_and_holds_new(self):
        self.con.execute("INSERT INTO stock(code,name,market,is_common,updated) VALUES('458650','성우','KOSDAQ',1,'2026-10-25')")
        self.con.commit()
        store_universe(self.con, self.rows, "2026-10-26 12:00", {"458650": record("458650", "다른회사")})
        self.assertEqual(dict(self.con.execute("SELECT code,is_common FROM stock")), {"458650": 1, "005935": 0})
        quality = json.loads(self.con.execute("SELECT v FROM meta WHERE k='security_types_quality'").fetchone()[0])
        self.assertEqual(quality["unverified_count"], 2)
        self.assertEqual(quality["status"], "warning")

    def test_public_quality_preserves_cache_age_and_collection_fallback(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "types.json"
            path.write_text(json.dumps(payload(), ensure_ascii=False), encoding="utf-8")
            stocks = {"458650": self.rows[0]}
            result = metadata_quality(path, stocks, {"458650"}, datetime(2026, 10, 27), {"status": "fallback"})
            self.assertEqual(result["age_days"], 1)
            self.assertEqual(result["status"], "warning")
            self.assertEqual(result["unverified_count"], 0)
            self.assertEqual(result["scope"], "current_reference_metadata")
            self.assertEqual(result["collection_status"], "fallback")


if __name__ == "__main__": unittest.main()
