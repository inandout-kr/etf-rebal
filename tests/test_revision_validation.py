import copy
import unittest

from validate_data import DataQualityError, validate_held_flow
from archive import from_flow, from_semi


class HeldFlowValidationTests(unittest.TestCase):
    def test_held_prediction_is_not_archived_as_zero_or_empty_result(self):
        self.assertIsNone(from_flow({'availability': 'hold'}, {}, '2026-10-26'))
        self.assertIsNone(from_semi({'scenarios': {'current': {'availability': 'hold'}}}, {}, '2026-10-26'))
    def event(self):
        return {'hold_reasons': [{'code': 'missing_ffr', 'message': '공식 FFR 없음'}],
                'adds': None, 'dels': None, 'n_target': None,
                'total_buy': None, 'total_sell': None,
                'rows': [{'code': '0009K0', 'w_target': None, 'delta': None,
                          'flow_total': None, 'adv_mult': None, 'flow_by_etf': {'139260': None}}]}

    def test_hold_is_unknown_not_zero(self):
        validate_held_flow(self.event(), 'it')
        for field in ('adds', 'dels', 'n_target', 'total_buy', 'total_sell'):
            with self.subTest(field=field):
                event = self.event()
                event[field] = [] if field in ('adds', 'dels') else 0
                with self.assertRaises(DataQualityError):
                    validate_held_flow(event, 'it')

    def test_target_and_etf_zero_are_rejected(self):
        for field in ('w_target', 'w_target_raw', 'delta', 'flow_total', 'adv_mult', 'flow_by_etf'):
            with self.subTest(field=field):
                event = self.event()
                event['rows'][0][field] = {'139260': 0} if field == 'flow_by_etf' else 0
                with self.assertRaises(DataQualityError):
                    validate_held_flow(event, 'it')

    def test_duplicate_code_and_missing_reason_rejected(self):
        event = self.event()
        event['rows'] += copy.deepcopy(event['rows'])
        with self.assertRaises(DataQualityError):
            validate_held_flow(event, 'it')
        event = self.event()
        event['hold_reasons'] = []
        with self.assertRaises(DataQualityError):
            validate_held_flow(event, 'it')
