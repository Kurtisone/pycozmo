import os
import tempfile
import unittest

import pycozmo
from pycozmo.json_loader import find_file, get_json_files, load_json_file


class TestJsonLoader(unittest.TestCase):

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.root = self.dir.name

    def _write(self, relative, content):
        fspec = os.path.join(self.root, relative)
        os.makedirs(os.path.dirname(fspec), exist_ok=True)
        with open(fspec, "w") as f:
            f.write(content)
        return fspec

    def test_find_file(self):
        self._write("a/b/target.json", "{}")
        self.assertEqual(find_file(self.root, "target.json"), os.path.join(self.root, "a", "b", "target.json"))

    def test_find_file_missing(self):
        # Declared Optional because it returns nothing when the file is not there; its caller tests the result.
        self.assertIsNone(find_file(self.root, "absent.json"))

    def test_load_strips_comments(self):
        # The resource files carry non-standard // comments that json itself rejects.
        fspec = self._write("conf.json", '{\n  "a": 1,  // trailing comment\n  "b": 2\n}\n')
        self.assertEqual(load_json_file(fspec), {"a": 1, "b": 2})

    def test_load_plain(self):
        fspec = self._write("plain.json", '{"a": [1, 2, 3]}')
        self.assertEqual(load_json_file(fspec), {"a": [1, 2, 3]})

    def test_get_json_files_from_directory(self):
        self._write("d/one.json", "{}")
        self._write("d/two.json", "{}")
        self._write("d/ignored.txt", "x")
        found = get_json_files(self.root, ["d"])
        self.assertEqual(sorted(os.path.basename(f) for f in found), ["one.json", "two.json"])

    def test_get_json_files_named(self):
        self._write("d/one.json", "{}")
        found = get_json_files(self.root, ["d/one.json"])
        self.assertEqual([os.path.basename(f) for f in found], ["one.json"])

    def test_get_json_files_leading_slash(self):
        # A leading slash in the resource name is stripped rather than making the path absolute.
        self._write("d/one.json", "{}")
        self.assertEqual(get_json_files(self.root, ["/d/one.json"]), get_json_files(self.root, ["d/one.json"]))


class TestFilter(unittest.TestCase):

    def test_empty_filter_allows(self):
        self.assertFalse(pycozmo.filter.Filter().filter(1))

    def test_allowed(self):
        f = pycozmo.filter.Filter()
        f.allow_ids({1, 2})
        self.assertFalse(f.filter(1))
        self.assertTrue(f.filter(3))

    def test_denied(self):
        f = pycozmo.filter.Filter()
        f.deny_ids({1})
        self.assertTrue(f.filter(1))
        self.assertFalse(f.filter(2))

    def test_none_is_never_filtered(self):
        # A packet id is optional, and both callers pass it straight through.
        f = pycozmo.filter.Filter()
        f.deny_ids({1})
        self.assertFalse(f.filter(None))
