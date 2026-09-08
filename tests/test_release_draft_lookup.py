"""Unit tests for draft-aware Release lookup; not installation or speech QA."""
import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('draft_import', ROOT/'scripts/import_public_release.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


class DraftLookup(unittest.TestCase):
    def release(self, **changes):
        return {'id': 123, 'tag_name': m.TAG, 'body': m.MARKER,
                'draft': True, 'prerelease': True, 'assets': [], **changes}

    def test_draft_resolved_by_numeric_id(self):
        release = self.release()
        with patch.object(m, 'gh_json', side_effect=[[release], release]) as api:
            self.assertTrue(m.reviewed_release()['draft'])
            self.assertEqual(api.call_args_list[1].args,
                             ('api', f'repos/{m.REPO}/releases/123'))
            self.assertNotIn('/tags/', str(api.call_args_list))

    def test_published_release_uses_same_identity_check(self):
        release = self.release(draft=False)
        with patch.object(m, 'gh_json', side_effect=[[release], release]):
            self.assertFalse(m.reviewed_release()['draft'])

    def test_unrelated_tag_owner_refused(self):
        with patch.object(m, 'gh_json', return_value=[self.release(body='unrelated')]):
            with self.assertRaisesRegex(RuntimeError, 'refusing takeover'):
                m.reviewed_release()

    def test_duplicate_fixed_tag_refused(self):
        with patch.object(m, 'gh_json', return_value=[self.release(), self.release(id=124)]):
            with self.assertRaisesRegex(RuntimeError, 'exactly one'):
                m.reviewed_release()

    def test_identity_changed_between_list_and_read(self):
        with patch.object(m, 'gh_json', side_effect=[[self.release()], self.release(id=999)]):
            with self.assertRaisesRegex(RuntimeError, 'identity changed'):
                m.reviewed_release()

    def test_missing_release_refused(self):
        with patch.object(m, 'gh_json', return_value=[]):
            with self.assertRaisesRegex(RuntimeError, 'exactly one'):
                m.reviewed_release()

    def test_second_page_supported(self):
        first = [self.release(id=i+1, tag_name=f'other-{i}') for i in range(100)]
        release = self.release(id=777)
        with patch.object(m, 'gh_json', side_effect=[first, [release], release]) as api:
            self.assertEqual(m.reviewed_release()['id'], 777)
            self.assertIn('page=2', api.call_args_list[1].args[1])

    def test_invalid_id_refused(self):
        with patch.object(m, 'gh_json', return_value=[self.release(id='../other')]):
            with self.assertRaisesRegex(RuntimeError, 'Invalid release ID'):
                m.reviewed_release()


if __name__ == '__main__':
    unittest.main()
