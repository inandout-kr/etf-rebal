"""Public artifacts must contain public provenance rather than private transfer IDs."""
import json
import re
import unittest
from datetime import datetime
from pathlib import Path

from classification_scenarios import load_mapping, stable_hash

ROOT = Path(__file__).resolve().parents[1]


class PublicMetadataTests(unittest.TestCase):
    def test_missing_mapping_error_does_not_publish_an_os_path(self):
        missing = ROOT / 'nonexistent_mapping_for_privacy_test.json'
        payload, error = load_mapping(missing)
        self.assertEqual(payload, {})
        self.assertIn('FileNotFoundError', error)
        self.assertNotIn(str(ROOT), error)

    def test_public_payloads_do_not_expose_private_transfer_ids_or_user_paths(self):
        patterns = (r'libfile_[a-f0-9]+', r'file_000[0-9a-f]+', r'[Cc]:[\\/]Users[\\/]',
                    r'Sentinel_', r'source_thread_id', r'source_orbit_id')
        for name in ('data/krics_mapping.json', 'data/methodology.json', 'data/analysis.json',
                     'dist/data.json', 'dist/index.html'):
            content = (ROOT / name).read_text(encoding='utf-8')
            for pattern in patterns:
                with self.subTest(file=name, pattern=pattern):
                    self.assertIsNone(re.search(pattern, content))

    def assert_export_source(self, source, schema):
        self.assertEqual(source['source_sha256'], stable_hash(source['mapping']))
        self.assertEqual(len(source['mapping']), source['expected_records'])
        self.assertNotIn('library_file_id', source)
        if schema == 1:
            self.assertRegex(source['source_id'], r'^user_krics_[a-z0-9_]+$')
            self.assertIn('not the original workbook', source['source_hash_basis'])
        else:
            self.assertEqual(schema, 2)
            self.assertEqual(source['source_id'], 'krx_public_krics_level1_2')
            self.assertEqual(source['source_read_location'],
                             'https://index.krx.co.kr/contents/MKD/03/0303/03030304/MKD03030304.jsp')
            self.assertEqual(source['source_hash_basis'], 'canonical exported code-sector pairs')

    def test_export_hash_describes_mapping_rows_for_the_declared_source_schema(self):
        payload = json.loads((ROOT / 'data/krics_mapping.json').read_text(encoding='utf-8'))
        self.assertIn(payload['schema_version'], (1, 2))
        for source in payload['sources'].values():
            self.assert_export_source(source, payload['schema_version'])

    def test_legacy_export_fixture_keeps_workbook_and_mapping_hash_meanings_separate(self):
        mapping = [['005930', '정보기술'], ['0009K0', '헬스케어']]
        source = {'source_id': 'user_krics_unit_fixture', 'source_sha256': stable_hash(mapping),
                  'mapping': mapping, 'expected_records': len(mapping),
                  'source_hash_basis': 'SHA256 of canonical exported mapping rows; not the original workbook'}
        self.assert_export_source(source, 1)
        with self.assertRaises(AssertionError):
            self.assert_export_source(dict(source, source_sha256='a' * 64), 1)
        with self.assertRaises(AssertionError):
            self.assert_export_source(dict(source, source_hash_basis='SHA256 of original workbook'), 1)

    def test_official_snapshot_separates_query_observation_and_unknown_validity(self):
        payload = json.loads((ROOT / 'data/krics_mapping.json').read_text(encoding='utf-8'))
        self.assertEqual(payload['schema_version'], 2)
        self.assertEqual(payload['mode'], 'official_current_snapshot')
        self.assertRegex(payload['requested_date'], r'^\d{8}$')
        datetime.strptime(payload['requested_date'], '%Y%m%d')
        observed = datetime.fromisoformat(payload['observed_at'])
        self.assertIsNotNone(observed.tzinfo)
        self.assertIs(payload['historical_asof_supported'], False)
        for field in ('returned_as_of', 'valid_from', 'valid_to'):
            self.assertIn(field, payload)
            self.assertIsNone(payload[field])
        validation = payload['validation']
        self.assertEqual(validation['sector_count'], 9)
        self.assertEqual(validation['group_count'], 24)
        self.assertIs(validation['summary_counts_verified'], True)
        self.assertIs(validation['level1_union_verified'], True)
        self.assertEqual(validation['official_records'], len(payload['records']))
        self.assertEqual(sum(validation['group_counts'].values()), len(payload['records']))
        self.assertEqual(len({r['code'] for r in payload['records']}), len(payload['records']))
        # Raw response digests cover query quote fields; exported source hashes
        # cover only canonical code-sector pairs. Neither is a workbook digest.
        self.assertEqual(len(validation['response_sha256']), 1 + 2 * (24 + 9))
        for digest in validation['response_sha256'].values():
            self.assertRegex(digest, r'^[0-9a-f]{64}$')
        self.assertIn('canonical JSON response', validation['response_hash_basis'])
        self.assertIn('effective date unavailable', validation['response_hash_basis'])
        self.assertEqual(payload['snapshot_id'], stable_hash({
            'observed_at': payload['observed_at'], 'requested_date': payload['requested_date'],
            'records': sorted(payload['records'], key=lambda r: r['code'])}))
