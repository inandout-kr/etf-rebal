"""Public artifacts must contain public provenance rather than private transfer IDs."""
import json
import re
import unittest
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

    def test_export_hash_describes_mapping_rows_and_not_a_private_workbook(self):
        payload = json.loads((ROOT / 'data/krics_mapping.json').read_text(encoding='utf-8'))
        for source in payload['sources'].values():
            self.assertTrue(source['source_id'].startswith('user_krics_'))
            self.assertEqual(source['source_sha256'], stable_hash(source['mapping']))
            self.assertEqual(len(source['mapping']), source['expected_records'])
            self.assertIn('not the original workbook', source['source_hash_basis'])
