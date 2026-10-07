import contextlib
import io
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from fetch_market import fetch_universe, validate_universe


def stock(code, name=None, close="6,200"):
    return {"stockEndType": "stock", "itemCode": code, "stockName": name or code,
            "closePrice": close, "marketValue": "737", "accumulatedTradingValue": "64", "accumulatedTradingVolume": "10,229"}


def response(stocks, total=1):
    return SimpleNamespace(raise_for_status=lambda: None, json=lambda: {"stocks": stocks, "totalCount": total})


class UniversePaginationTests(unittest.TestCase):
    def collect(self, replies):
        with patch("fetch_market.requests.get", side_effect=replies), contextlib.redirect_stdout(io.StringIO()):
            return fetch_universe()

    def test_moving_pages_merge_duplicate_code_and_keep_last_quote(self):
        rows = self.collect([
            response([stock("005930"), stock("000660")], 101),
            response([stock("000660", close="6,300")], 101),
            response([stock("040160", "누리플렉스")]),
        ])
        self.assertEqual([r["code"] for r in rows], ["005930", "000660", "040160"])
        self.assertEqual(rows[1]["close"], 6300)
        validate_universe(rows, 3)

    def test_identical_duplicate_from_live_failure_is_accepted(self):
        rows = self.collect([
            response([stock("005930")]),
            response([stock("040160", "누리플렉스")], 101),
            response([stock("040160", "누리플렉스")], 101),
        ])
        self.assertEqual(len(rows), 2)
        validate_universe(rows, 2)

    def test_duplicate_identity_conflict_is_rejected(self):
        for second in (stock("005930", "다른 이름"), stock("005930")):
            if second["stockName"] == "다른 이름":
                replies = [response([stock("005930")], 101), response([second], 101)]
            else:
                replies = [response([stock("005930")]), response([second])]
            with self.subTest(second=second["stockName"]), self.assertRaisesRegex(ValueError, "이름/시장 불일치.*005930"):
                self.collect(replies)

    def test_completeness_counts_unique_codes_after_deduplication(self):
        rows = self.collect([
            response([stock("005930"), stock("005930")]),
            response([stock("040160"), stock("040160")]),
        ])
        with self.assertRaisesRegex(ValueError, "고유 2종목.*기존 4종목.*90%"):
            validate_universe(rows, 4)

    def test_missing_market_and_empty_response_have_distinct_errors(self):
        with self.assertRaisesRegex(ValueError, "비었습니다"):
            validate_universe([], 3)
        rows = self.collect([response([stock("005930")]), response([])])
        with self.assertRaisesRegex(ValueError, "시장 누락/오류"):
            validate_universe(rows)

    def test_validator_still_rejects_unnormalized_duplicates(self):
        rows = self.collect([response([stock("005930")]), response([stock("040160")])])
        with self.assertRaisesRegex(ValueError, "중복 코드: 전체 3행 / 고유 2종목"):
            validate_universe(rows + [dict(rows[0])])

    def test_required_quote_validation_is_preserved(self):
        rows = self.collect([response([stock("005930", close="0")]), response([stock("040160")])])
        with self.assertRaisesRegex(ValueError, "필수 값"):
            validate_universe(rows)


if __name__ == "__main__":
    unittest.main()
