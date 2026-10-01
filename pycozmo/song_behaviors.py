"""

Behaviors that sing and dance.

Singing is one behavior class for 39 songs: each behavior names its song as a WWise switch - a value of the group
of songs at its tempo - and plays the same animations, a get-in, the song at its tempo, and a get-out. The song's
animation names the event that sings; which song it sings, the switch says: see pycozmo.songs. The Singing
activity has Cozmo sing now and then in freeplay, more often as its play need is met.

"""

import re
import threading
from typing import Optional

from .cube_behaviors import BehaviorScript


__all__ = [
    "BehaviorSinging",
    "BehaviorDance",
]


class BehaviorSinging(BehaviorScript):
    """ Singing - sing a song: Anki's animations, with the song the configuration names. """

    def song(self) -> Optional[str]:
        song = self.conf.get("audioSwitch")
        return str(song) if song else None

    def tempo_trigger(self) -> Optional[str]:
        """ The animation of a song at the song's tempo, which its switch group names: Singing_100bpm, say. """
        match = re.search(r"(\d+)Bpm$", self.conf.get("audioSwitchGroup", ""), re.IGNORECASE)
        return "Singing_{}bpm".format(match.group(1)) if match else None

    def wants_to_run(self) -> bool:
        trigger, song = self.tempo_trigger(), self.song()
        return trigger is not None and song is not None and trigger in self.cli.animation_groups and \
            self.cli.audio_library.has_song(self.conf["audioSwitchGroup"], song)

    def script(self, cancel: threading.Event) -> None:
        trigger, song = self.tempo_trigger(), self.song()
        if trigger is None or song is None:
            return
        self.cli.set_audio_switch(self.conf["audioSwitchGroup"], song)
        self.play("Singing_GetIn", cancel)
        if self.play(trigger, cancel):
            self.play("Singing_GetOut", cancel)
            self.need_action()


class BehaviorDance(BehaviorScript):
    """ Dance - play the dance's animations one after another. """

    def wants_to_run(self) -> bool:
        triggers = self.conf.get("animTriggers", [])
        return bool(triggers) and all(trigger in self.cli.animation_groups for trigger in triggers)

    def script(self, cancel: threading.Event) -> None:
        for trigger in self.conf.get("animTriggers", []):
            if not self.play(trigger, cancel):
                return
        self.need_action()
