"""

AudioKinetic WWise SoundBank representation and reading.

References:
    - http://wiki.xentax.com/index.php/Wwise_SoundBank_(*.bnk)
    - https://github.com/rickvg/Wwise-BNKExtract

"""

from typing import Any, BinaryIO, Dict, Iterable, List, Optional
import struct
from ._chunk import Chunk

from . import exception
from . import soundbanksinfo


#: HIRC object types that hold other objects: random/sequence, switch, actor-mixer, blend.
CONTAINER_TYPES = (5, 6, 7, 9)

#: Largest child count accepted when looking for a container's child list. Cozmo's biggest
#: container holds 40 takes.
MAX_CONTAINER_CHILDREN = 64


class File:
    """ AudioKinetic WWise WEM File. """

    __slots__ = [
        "soundbank_id",
        "id",
        "offset",
        "length",
    ]

    def __init__(self, soundbank_id: int, file_id: int, offset: int, length: int) -> None:
        # SoundBank ID.
        self.soundbank_id = int(soundbank_id)
        # File ID.
        self.id = int(file_id)
        # Offset from start of DATA chunk.
        self.offset = int(offset)
        # Length in bytes.
        self.length = int(length)


class Container:
    """
    AudioKinetic WWise container - a node holding other nodes.

    Cozmo's sound bank holds 468 random/sequence containers, 21 switch containers, 20 actor-mixers
    and 5 blend containers. They are what gives a reaction its variants: the event names the
    container, and the container names the takes to choose between.
    """

    __slots__ = [
        "soundbank_id",
        "id",
        "type",
        "children",
    ]

    def __init__(self, soundbank_id: int, container_id: int, container_type: int,
                 children: Iterable[int]) -> None:
        # SoundBank ID.
        self.soundbank_id = int(soundbank_id)
        # Container ID.
        self.id = int(container_id)
        # HIRC object type.
        self.type = int(container_type)
        # IDs of the contained objects, which may be sounds or containers themselves.
        self.children = list(children)


class SFX:
    """ AudioKinetic WWise sound effect/voice. """

    __slots__ = [
        "soundbank_id",
        "id",
        "name",
        "location",
        "file_id",
        "length",
        "type",
    ]

    def __init__(self, soundbank_id: int, sfx_id: int, name: str,
                 location: int, file_id: int, length: int, sfx_type: int) -> None:
        # SoundBank ID.
        self.soundbank_id = int(soundbank_id)
        # SFX ID.
        self.id = int(sfx_id)
        # SFX from SoundbankInfo.xml, if available.
        self.name = str(name)
        # Location.
        #   - 0: "embedded"
        #       - the file is in the SoundBank
        #       - length is the same as in DIDX
        #   - 1: "streamed"
        #       - the file is in the SoundBank
        #       - length is the same as in DIDX
        #   - 2: "streamed, zero latency"
        #       - the file is in the file-system as .wem
        #       - length does not match file size?
        self.location = int(location)
        # File ID.
        self.file_id = int(file_id)
        # Length in bytes.
        self.length = int(length)
        # Type:
        #   - 0: SFX
        #   - 1: voice
        self.type = int(sfx_type)


class EventAction:
    """ AudioKinetic WWise Event Action. """

    __slots__ = [
        "soundbank_id",
        "id",
        "scope",
        "type",
        "reference_id"
    ]

    def __init__(self, soundbank_id: int, ea_id: int, scope: int, ea_type: int, reference_id: int) -> None:
        # SoundBank ID.
        self.soundbank_id = int(soundbank_id)
        # Event action ID.
        self.id = int(ea_id)
        # Scope.
        self.scope = int(scope)
        # Event action ID.
        self.type = int(ea_type)
        # Event action ID.
        self.reference_id = int(reference_id)


class Event:
    """ AudioKinetic WWise Event. """

    __slots__ = [
        "soundbank_id",
        "id",
        "name",
        "action_ids"
    ]

    def __init__(self, soundbank_id: int, event_id: int, name: str, action_ids: Iterable[int]) -> None:
        # SoundBank ID.
        self.soundbank_id = int(soundbank_id)
        # Event ID.
        self.id = int(event_id)
        # Event name from SoundbankInfo.xml, if available.
        self.name = str(name)
        # Action IDs.
        self.action_ids = action_ids


class SoundBank:
    """ AudioKinetic WWise SoundBank (.bnk) file representation class. """

    __slots__ = [
        "fspec",
        "id",
        "name",
        "version",
        "data_offset",
        "objs",
    ]

    def __init__(self) -> None:
        # File specification.
        self.fspec = ""
        # SoundBank ID.
        self.id = -1
        # SoundBank name from SoundbankInfo.xml, if available.
        self.name = ""
        # SoundBank version.
        self.version = -1
        # DATA chunk start offset for loading WEM files.
        self.data_offset = -1
        # Object dictionary (ID -> File/SFX/EventAction/Event).
        self.objs: Dict[int, Any] = {}


class SoundBankReader:

    def __init__(self, soundbankinfo: Dict[int, Any]) -> None:
        self.soundbankinfo = soundbankinfo
        # The sound bank being read. Set for the duration of load_file() and cleared again afterwards, so the
        # _read_* methods below assert it rather than handling its absence.
        self._soundbank: Optional[SoundBank] = None

    @staticmethod
    def _read_uint8(chunk: Chunk) -> int:
        """ Read uint8 value from chunk. """
        value: int = struct.unpack('B', chunk.read(1))[0]
        return value

    @staticmethod
    def _read_uint32(chunk: Chunk) -> int:
        """ Read uint32 value from chunk. """
        value: int = struct.unpack('<L', chunk.read(4))[0]
        return value

    def _read_header(self, f: BinaryIO) -> None:
        """ Read Bank Header (BKHD) chunk. """
        assert self._soundbank is not None
        chunk = Chunk(f, bigendian=False, align=False)
        if chunk.getname() != b"BKHD":
            raise exception.AudioKineticFormatError("Not an AudioKinetic WWise SoundBank file.")
        self._soundbank.version = self._read_uint32(chunk)
        self._soundbank.id = self._read_uint32(chunk)
        # There are additional 6 unknown uint32 values.
        chunk.skip()
        # Get name from SoundBankInfo
        info = self.soundbankinfo.get(self._soundbank.id)
        self._soundbank.name = info.name if isinstance(info, soundbanksinfo.SoundBankInfo) else ""

    def _read_data_index(self, chunk: Chunk) -> None:
        """ Read Data Index (DIDX) chunk."""
        assert self._soundbank is not None
        num_objects = chunk.getsize() // 12
        for i in range(num_objects):
            try:
                file_id, offset, length = struct.unpack("<LLL", chunk.read(12))
            except struct.error as e:
                raise exception.AudioKineticIOError("Failed to read Data Index (DIDX) chunk.") from e
            assert file_id
            assert length
            assert file_id not in self._soundbank.objs
            self._soundbank.objs[file_id] = File(self._soundbank.id, file_id, offset, length)

    def _read_hirc(self, chunk: Chunk) -> None:
        """ Read HIRC chunk. """
        assert self._soundbank is not None
        num_objects = self._read_uint32(chunk)
        for i in range(num_objects):
            try:
                object_type, object_len, object_id = struct.unpack("<BLL", chunk.read(9))
            except struct.error as e:
                raise exception.AudioKineticIOError("Failed to read HIRC chunk.") from e
            assert object_id
            assert object_type
            assert object_len
            object_len -= 4
            obj_data = chunk.read(object_len)
            if object_type == 2:
                # Sound effect/voice
                try:
                    _, location, file_id, length, sfx_type = struct.unpack_from('<LBLLL', obj_data)
                except struct.error as e:
                    raise exception.AudioKineticIOError("Failed to read SFX object.") from e
                assert location in (0, 1, 2)
                assert file_id
                assert sfx_type in (0, 1)
                assert object_id not in self._soundbank.objs
                # Get name from SoundBankInfo
                info = self.soundbankinfo.get(object_id)
                name = info.name if isinstance(info, soundbanksinfo.FileInfo) else ""
                assert object_id not in self._soundbank.objs
                self._soundbank.objs[object_id] = SFX(
                    self._soundbank.id, object_id, name, location, file_id, length, sfx_type)
            elif object_type == 3:
                # Event Action
                try:
                    act_scope, act_type, reference_id, zero = struct.unpack_from('<BBLB', obj_data)
                except struct.error as e:
                    raise exception.AudioKineticIOError("Failed to read event action object.") from e
                assert act_scope in (1, 2, 3, 4, 8)
                assert act_type in (1, 2, 3, 4, 18, 19, 25, 30, 33)
                assert zero == 0
                assert object_id not in self._soundbank.objs
                self._soundbank.objs[object_id] = EventAction(
                    self._soundbank.id, object_id, act_scope, act_type, reference_id)
            elif object_type == 4:
                # Event
                action_ids = []
                try:
                    num_act_ids = struct.unpack_from('<L', obj_data)[0]
                except struct.error as e:
                    raise exception.AudioKineticIOError("Failed to event object.") from e
                for j in range(num_act_ids):
                    try:
                        action_id = struct.unpack_from('<L', obj_data, 4 + j * 4)[0]
                    except struct.error as e:
                        raise exception.AudioKineticIOError("Failed to read event object.") from e
                    assert action_id
                    action_ids.append(action_id)
                # Get name from SoundBankInfo
                info = self.soundbankinfo.get(object_id)
                name = info.name if isinstance(info, soundbanksinfo.EventInfo) else ""
                assert object_id not in self._soundbank.objs
                self._soundbank.objs[object_id] = Event(
                    self._soundbank.id, object_id, name, action_ids)
            elif object_type in CONTAINER_TYPES:
                # Random/sequence container, switch container, actor-mixer or blend container: the
                # objects that hold the variants of a sound. An event points at one of these far
                # more often than straight at a sound - 331 of the 380 audio events Cozmo's
                # animations name do - so without them an event resolves to nothing.
                children = self._find_children(obj_data)
                if children is not None:
                    self._soundbank.objs[object_id] = Container(
                        self._soundbank.id, object_id, object_type, children)
            else:
                # Skip unknown objects.
                pass

    @staticmethod
    def _find_children(obj_data: bytes) -> Optional[List[int]]:
        """
        Find a container's list of children.

        A container starts with NodeBaseParams, whose length depends on how many effects, RTPCs and
        state chunks the object carries, so the child list does not sit at a fixed offset. Rather
        than track every field across WWise versions, this looks for what a child list has to look
        like: a count followed by exactly that many distinct object identifiers. On Cozmo's own
        sound bank that is unambiguous for 491 of the 514 containers, and the longest candidate is
        taken for the rest. Resolving the events this way agrees with the file names WWise recorded
        in SoundbanksInfo.xml.
        """
        candidates = []
        for offset in range(0, len(obj_data) - 4):
            count = struct.unpack_from("<L", obj_data, offset)[0]
            if not 1 <= count <= MAX_CONTAINER_CHILDREN or offset + 4 + 4 * count > len(obj_data):
                continue
            ids = struct.unpack_from("<" + "L" * count, obj_data, offset + 4)
            if 0 in ids or len(set(ids)) != count:
                continue
            candidates.append((count, -offset, list(ids)))
        if not candidates:
            return None
        return max(candidates)[2]

    def load_file(self, f: BinaryIO, fspec: str) -> SoundBank:
        """ Load a SoundBank .bnk file object and return a SoundBank object. """

        self._soundbank = SoundBank()
        self._soundbank.fspec = fspec

        # Read header.
        try:
            self._read_header(f)
        except (EOFError, OSError, ValueError, RuntimeError) as e:
            raise exception.AudioKineticIOError("Failed to read SoundBank file header.") from e

        # Read subsequent chunks.
        while True:
            try:
                chunk = Chunk(f, bigendian=False, align=False)
                chunkname = chunk.getname()
                if chunkname == b"DIDX":
                    self._read_data_index(chunk)
                elif chunkname == b"DATA":
                    self._soundbank.data_offset = f.tell()
                elif chunkname == b"HIRC":
                    self._read_hirc(chunk)
                else:
                    # Skip other unknown chunks (e.g. INIT, STMG, ENVS, PLAT).
                    pass
                chunk.skip()
            except EOFError:
                # Reached end of file.
                break
            except (OSError, ValueError, RuntimeError) as e:
                raise exception.AudioKineticIOError("Failed reading SoundBank file.") from e

        soundbank = self._soundbank
        assert soundbank is not None
        self._soundbank = None

        return soundbank

    def load(self, fspec: str) -> SoundBank:
        """ Load a SoundBank .bnk file and return a SoundBank object. """
        with open(fspec, "rb") as f:
            return self.load_file(f, fspec)
