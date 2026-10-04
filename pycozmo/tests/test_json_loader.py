import os
import tempfile
import unittest
from typing import Optional

import pycozmo
from pycozmo import json_loader
from pycozmo.json_loader import find_file, get_json_files, load_json_file

from .test_brain import cozmo_assets_available


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


def walked(directory: str, name: str) -> Optional[str]:
    """ What find_file() used to do: walk the tree for every file asked for. """
    for root, _, files in os.walk(directory):
        if name in files:
            return os.path.join(root, name)
    return None


class TestFindFileIndex(unittest.TestCase):

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = self.directory.name

    def put(self, *parts: str) -> str:
        path = os.path.join(self.root, *parts)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            f.write("{}")
        return path

    def test_it_finds_a_file_in_the_tree(self):
        path = self.put("a", "b", "x.json")
        self.assertEqual(json_loader.find_file(self.root, "x.json"), path)

    def test_a_file_that_is_not_there_is_none(self):
        self.put("a", "x.json")
        self.assertIsNone(json_loader.find_file(self.root, "y.json"))

    def test_a_directory_that_is_not_there_is_none(self):
        self.assertIsNone(json_loader.find_file(os.path.join(self.root, "nothing"), "x.json"))

    def test_the_first_of_two_files_of_one_name_is_the_one_walking_finds(self):
        self.put("a", "x.json")
        self.put("b", "c", "x.json")
        self.put("x.json")
        self.assertEqual(json_loader.find_file(self.root, "x.json"), walked(self.root, "x.json"))

    def test_a_file_put_in_the_directory_after_is_found(self):
        self.put("a", "x.json")
        self.assertIsNone(json_loader.find_file(self.root, "late.json"))
        # The directory's modification time moves a little more than a second's tenth: make sure it does.
        stat = os.stat(self.root)
        os.utime(self.root, (stat.st_atime, stat.st_mtime - 5.0))
        json_loader.find_file(self.root, "x.json")
        path = self.put("late.json")
        self.assertEqual(json_loader.find_file(self.root, "late.json"), path)

    @unittest.skipUnless(cozmo_assets_available(), "Cozmo's resources are not downloaded")
    def test_it_finds_what_walking_finds_in_cozmo_s_resources(self):
        from pycozmo import util
        directory = str(util.get_cozmo_asset_dir())
        for name in ("AnimationTriggerMap.json", "CubeAnimationTriggerMap.json", "no_such_file.json"):
            with self.subTest(name=name):
                self.assertEqual(json_loader.find_file(directory, name), walked(directory, name))
