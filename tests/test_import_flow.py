"""Import safety unit tests. These do NOT claim a real Release was published."""
import importlib.util
import io
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch
import zipfile

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('release_import', ROOT/'scripts/import_public_release.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


def archive(entries):
    out = io.BytesIO()
    with zipfile.ZipFile(out,'w',zipfile.ZIP_DEFLATED) as z:
        for name, content in entries:
            z.writestr(name,content)
    return out.getvalue()


class ImportGuards(unittest.TestCase):
    def test_approved_digest_and_repository_are_fixed(self):
        self.assertEqual(m.REPO,'suwujin-code/soulx-studio')
        self.assertEqual(len(bytes.fromhex(m.BUNDLE_SHA)),32)
        self.assertEqual(m.BUNDLE,'SoulX-Public-Import.zip')

    def test_normal_archive(self):
        self.assertEqual(m.safe_entries(archive([('docs/a.txt',b'OK')]),max_total=10),{'docs/a.txt':b'OK'})

    def test_parent_and_absolute_paths_rejected(self):
        for name in ['../escape','/absolute','a/../../escape','C:/escape','a\\escape']:
            with self.subTest(name=name), self.assertRaises(ValueError):
                m.safe_entries(archive([(name,b'bad')]),max_total=20)

    def test_case_collision_rejected(self):
        with self.assertRaises(ValueError):
            m.safe_entries(archive([('A.py',b'a'),('a.py',b'b')]),max_total=20)

    def test_symlink_rejected(self):
        out=io.BytesIO()
        with zipfile.ZipFile(out,'w') as z:
            info=zipfile.ZipInfo('link')
            info.create_system=3
            info.external_attr=(stat.S_IFLNK|0o777)<<16
            z.writestr(info,'../../private')
        with self.assertRaises(ValueError):
            m.safe_entries(out.getvalue(),max_total=100)

    def test_expanded_limit(self):
        with self.assertRaises(ValueError):
            m.safe_entries(archive([('a.txt',b'123456')]),max_total=5)

    def test_altered_bundle_refused_before_publication(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/m.BUNDLE
            p.write_bytes(archive([('manifest.json',b'{}')]))
            with self.assertRaisesRegex(ValueError,'SHA-256 mismatch'):
                m.verify_bundle(p)

    def test_unrelated_repository_refused(self):
        with patch.dict(os.environ,{'GITHUB_REPOSITORY':'other/repository'},clear=True):
            with self.assertRaisesRegex(RuntimeError,'authorized owner'):
                m.publish(ROOT,ROOT/m.BUNDLE)

    def test_missing_auth_refused(self):
        with patch.dict(os.environ,{'GITHUB_REPOSITORY':m.REPO,'GITHUB_REF':'refs/heads/main'},clear=True):
            with self.assertRaisesRegex(RuntimeError,'authentication'):
                m.publish(ROOT,ROOT/m.BUNDLE)

    def test_non_main_refused(self):
        with patch.dict(os.environ,{'GITHUB_REPOSITORY':m.REPO,'GITHUB_REF':'refs/heads/untrusted'},clear=True):
            with self.assertRaisesRegex(RuntimeError,'restricted to main'):
                m.publish(ROOT,ROOT/m.BUNDLE)


if __name__ == '__main__':
    unittest.main()
