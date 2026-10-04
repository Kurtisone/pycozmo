"""

JSON reading functions for files containing non-standard comments

"""

import json
import os
import threading
from typing import Dict, List, Optional, Tuple


def load_json_file(filename: str) -> Dict:
    with open(filename, 'r') as f:
        filtered_json = ''
        for line in f.readlines():
            # get all characters before '//'
            filtered_json += line.split('//')[0]
        parsed: Dict = json.loads(filtered_json)
        return parsed


def get_json_files(resource_dir: str, base_names: List[str]) -> List[str]:
    file_addr = []

    for name in base_names:
        if name[0] == '/':
            name = name[1:]
        addr = os.path.join(resource_dir, name)
        if os.path.isdir(addr):
            for root, _, files in os.walk(addr):
                for name in files:
                    if name.endswith('.json'):
                        file_addr.append(os.path.join(root, name))

        elif addr.endswith('.json'):
            file_addr.append(addr)

    return file_addr


# The files under a directory, by name: where the first of them is, as walking the tree finds it. Finding a file used to
# walk the whole tree, and the animation groups' 573 triggers walked it 573 times - 7 s of the 8 s load_anims() takes,
# 75 000 directories visited.
_FILE_INDEXES: Dict[Tuple[str, float], Dict[str, str]] = {}
_FILE_INDEXES_LOCK = threading.Lock()


def _index(directory: str) -> Dict[str, str]:
    # A tree changed since is walked again: the directory's own modification time is part of the key.
    try:
        key = (os.path.abspath(directory), os.stat(directory).st_mtime)
    except OSError:
        return {}
    with _FILE_INDEXES_LOCK:
        index = _FILE_INDEXES.get(key)
        if index is None:
            index = {}
            for root, _, files in os.walk(directory):
                for name in files:
                    index.setdefault(name, os.path.join(root, name))
            _FILE_INDEXES[key] = index
        return index


def find_file(directory: str, name: str) -> Optional[str]:
    return _index(directory).get(name)
