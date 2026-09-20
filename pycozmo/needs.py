"""

Nurture need representation and reading.

Cozmo has three needs - Repair, Energy and Play - which sit between 0.03 and 1.0, start full, and
fall on their own. They are not the mood: an emotion is a short lived shove that decays back to
nothing within a couple of minutes, while a need falls over hours and only something done to the
robot puts it back. Together they are what makes a robot left alone gradually ask to be played with,
fed and mended.

Four pieces of Anki's configuration describe them, all under cozmo_resources/config/engine:

- needs_config.json: the bounds, the level each need starts at, the four brackets each need is read
  in - Full, Normal, Warning, Critical - and how long a full need waits before it starts falling.
- needs_decay_config.json: how fast each need falls, which depends on the bracket it is in, and how
  one need at a low level makes another fall faster.
- needs_action_config.json: what everything the robot or the player can do is worth, as a change to
  each need. Ninety actions, most of them worth Play alone.
- needs_handlers_config.json: how badly the face glitches as Repair falls, which is not read here.

The decay rates come in a connected and an unconnected set, the second a hundred to a thousand times
slower, for the hours the application is not talking to the robot. PyCozmo is the application, so
only the connected rates are read.

"""

import os
import random
import time
from typing import Dict, List, Optional, Sequence, Tuple, TypeVar

from .logger import logger
from .json_loader import load_json_file


__all__ = [
    "NEEDS",
    "BRACKETS",

    "Thresholded",
    "DecayRate",
    "DecayModifier",
    "Need",
    "NeedAction",
    "Needs",

    "load_needs",
]


#: The needs, in the order a lower one gives way to a higher one. Repair comes first: a robot that
#: is both broken and starving asks to be mended, which is what needsSevereLowEnergy defers to
#: through its higherPriorityStrategyConfig.
NEEDS = ("Repair", "Energy", "Play")

#: The brackets a need is read in, highest first.
BRACKETS = ("Full", "Normal", "Warning", "Critical")


class Thresholded:
    """ Anything the configuration applies from a level upwards. """

    __slots__ = ["threshold"]

    def __init__(self, threshold: float) -> None:
        self.threshold = float(threshold)


class DecayRate(Thresholded):
    """ How fast a need falls while it is at or above a level. """

    __slots__ = ["per_minute"]

    def __init__(self, threshold: float, per_minute: float) -> None:
        super().__init__(threshold)
        self.per_minute = float(per_minute)


class DecayModifier(Thresholded):
    """ What one need being at or above a level does to the speed other needs fall at. """

    __slots__ = ["multipliers"]

    def __init__(self, threshold: float, multipliers: Dict[str, float]) -> None:
        super().__init__(threshold)
        #: Multiplier for each other need, by name.
        self.multipliers = dict(multipliers)


T = TypeVar("T", bound=Thresholded)


def _pick(entries: Sequence[T], level: float) -> Optional[T]:
    """
    The entry that applies at a level: the highest threshold the level still reaches.

    Anki writes these lists in no particular order and with the thresholds overlapping, so the
    choice is made by threshold rather than by position.
    """
    best: Optional[T] = None
    for entry in entries:
        if level >= entry.threshold and (best is None or entry.threshold > best.threshold):
            best = entry
    return best


class Need:
    """ One need: a level that falls on its own and that actions put back. """

    __slots__ = [
        "name",
        "level",
        "minimum",
        "maximum",
        "brackets",
        "decay_rates",
        "decay_modifiers",
        "fullness_cooldown",
        "last_decay_time",
        "full_since",
    ]

    def __init__(self,
                 name: str,
                 level: float,
                 minimum: float,
                 maximum: float,
                 brackets: Dict[str, float],
                 decay_rates: Optional[List[DecayRate]] = None,
                 decay_modifiers: Optional[List[DecayModifier]] = None,
                 fullness_cooldown: float = 0.0,
                 now: Optional[float] = None) -> None:
        now = time.perf_counter() if now is None else now
        self.name = str(name)
        self.minimum = float(minimum)
        self.maximum = float(maximum)
        self.level = min(max(float(level), self.minimum), self.maximum)
        #: The level at or above which the need is read in each bracket.
        self.brackets = dict(brackets)
        self.decay_rates = list(decay_rates or ())
        #: What this need's own level does to the other needs' decay.
        self.decay_modifiers = list(decay_modifiers or ())
        #: How long the need holds at full before it starts falling.
        self.fullness_cooldown = float(fullness_cooldown)
        # When decay was last applied, and since when the need has been full. See update() .
        self.last_decay_time = now
        self.full_since: Optional[float] = now if self.is_full else None

    @property
    def is_full(self) -> bool:
        return self.level >= self.brackets.get("Full", self.maximum)

    @property
    def bracket(self) -> str:
        """ The bracket the level is read in. """
        for name in BRACKETS:
            threshold = self.brackets.get(name)
            if threshold is not None and self.level >= threshold:
                return name
        return BRACKETS[-1]

    def in_bracket(self, name: str) -> bool:
        """ Whether the need is in a bracket, which is what the behaviors and activities ask. """
        return self.bracket == name

    @property
    def decay_per_minute(self) -> float:
        """ How fast the need is falling at its current level. """
        rate = _pick(self.decay_rates, self.level)
        return rate.per_minute if rate is not None else 0.0

    def multiplier_for(self, other: str) -> float:
        """ What this need's level does to another need's decay. """
        applicable = [m for m in self.decay_modifiers if other in m.multipliers]
        modifier = _pick(applicable, self.level)
        return modifier.multipliers[other] if modifier is not None else 1.0

    def decay(self, periods: float, multiplier: float = 1.0) -> None:
        """ Take a number of decay periods off the level. """
        if periods <= 0.0:
            return
        self.add(-self.decay_per_minute * periods * multiplier)

    def add(self, delta: float, now: Optional[float] = None) -> None:
        """ Shift the level, holding it within its bounds. """
        now = time.perf_counter() if now is None else now
        was_full = self.is_full
        self.level = min(max(self.level + delta, self.minimum), self.maximum)
        # Coming back up to full starts the fullness cooldown afresh; falling below ends it.
        if self.is_full and not was_full:
            self.full_since = now
        elif not self.is_full:
            self.full_since = None

    def holds_at_full(self, now: float) -> bool:
        """ Whether the need is full and still inside the wait before it starts falling. """
        if self.full_since is None or not self.is_full:
            return False
        return now - self.full_since < self.fullness_cooldown


class NeedAction:
    """ What one thing done to or by the robot is worth, as a change to each need. """

    __slots__ = ["action_id", "deltas", "cooldown", "spark_weight", "last_applied_time"]

    def __init__(self,
                 action_id: str,
                 deltas: Dict[str, Tuple[float, float]],
                 cooldown: float = 0.0,
                 spark_weight: float = 0.0) -> None:
        self.action_id = str(action_id)
        #: The change to each need, as the figure and the range it is drawn within.
        self.deltas = dict(deltas)
        self.cooldown = float(cooldown)
        self.spark_weight = float(spark_weight)
        self.last_applied_time: Optional[float] = None

    @classmethod
    def from_json(cls, data: Dict) -> "NeedAction":
        deltas = {}
        for need in NEEDS:
            key = need.lower()
            delta = float(data.get(key + "Delta", 0.0))
            spread = float(data.get(key + "Range", 0.0))
            if delta or spread:
                deltas[need] = (delta, spread)
        return cls(action_id=data["actionId"],
                   deltas=deltas,
                   cooldown=float(data.get("cooldownSecs", 0.0)),
                   spark_weight=float(data.get("freeplaySparksRewardWeight", 0.0)))

    def is_ready(self, now: float) -> bool:
        """ Whether the action's own cooldown has run out. Only SayName and SeeFace carry one. """
        if not self.cooldown or self.last_applied_time is None:
            return True
        return now - self.last_applied_time >= self.cooldown

    def sample(self) -> Dict[str, float]:
        """
        The change to each need this time round.

        The configuration gives a figure and a range beside it. The range is read as a spread either
        side of the figure, which is what its size suggests: Feed is worth 0.33 of Energy give or
        take 0.005, and a win at Keep Away 0.75 of Play give or take 0.03.
        """
        return {need: delta + random.uniform(-spread, spread) if spread else delta
                for need, (delta, spread) in self.deltas.items()}


class Needs:
    """ The robot's three needs, and everything that can change them. """

    __slots__ = ["needs", "actions", "decay_period", "_decay_debt"]

    def __init__(self,
                 needs: Dict[str, Need],
                 actions: Optional[Dict[str, NeedAction]] = None,
                 decay_period: float = 60.0) -> None:
        self.needs = dict(needs)
        self.actions = dict(actions or {})
        #: How often decay is applied, which is also what the rates are quoted per.
        self.decay_period = float(decay_period)
        self._decay_debt = 0.0

    def __getitem__(self, name: str) -> Need:
        return self.needs[name]

    def level(self, name: str) -> float:
        """ One need's level, or 1.0 for a need that is not held. """
        need = self.needs.get(name)
        return need.level if need is not None else 1.0

    def levels(self) -> Dict[str, float]:
        return {name: need.level for name, need in self.needs.items()}

    def bracket(self, name: str) -> str:
        """ One need's bracket, or Full for a need that is not held. """
        need = self.needs.get(name)
        return need.bracket if need is not None else BRACKETS[0]

    def in_bracket(self, name: str, bracket: str) -> bool:
        need = self.needs.get(name)
        return need.in_bracket(bracket) if need is not None else bracket == BRACKETS[0]

    def update(self, now: Optional[float] = None) -> None:
        """
        Let time pass.

        Anki's engine steps the needs once per decay period rather than continuously, and the rates
        are quoted per minute against a period of one, so a whole period is applied at a time and
        what is left over is carried. The rate a need falls at depends on the bracket it is in, so
        stepping and interpolating do not agree, and stepping is what the robot did.
        """
        now = time.perf_counter() if now is None else now
        if not self.needs or self.decay_period <= 0.0:
            return
        elapsed = now - max(need.last_decay_time for need in self.needs.values())
        if elapsed < 0.0:
            # The clock went backwards, which perf_counter does not, but a test may.
            for need in self.needs.values():
                need.last_decay_time = now
            return
        periods = int((elapsed + self._decay_debt) // self.decay_period)
        if periods < 1:
            return
        self._decay_debt = (elapsed + self._decay_debt) - periods * self.decay_period
        # One period at a time, however many have gone by. The rate a need falls at depends on the
        # bracket it is in, so applying ten periods at the rate that held ten periods ago is not the
        # same thing - and more than one period goes by whenever the caller is slow, which is what
        # happens when the heartbeat thread stalls.
        for period in range(periods):
            period_time = now - (periods - 1 - period) * self.decay_period
            # Every need falls against the same picture of the others, so the multipliers are read
            # before any level moves. Otherwise the order the needs happen to be held in would
            # matter.
            multipliers = {name: self.multiplier_for(name) for name in self.needs}
            for name, need in self.needs.items():
                if need.holds_at_full(period_time):
                    continue
                need.decay(1, multipliers[name])
        for need in self.needs.values():
            need.last_decay_time = now

    def multiplier_for(self, name: str) -> float:
        """
        How much faster a need falls because of where the others are.

        Only one of these matters in Anki's own configuration: Repair between 0.03 and 0.3 makes
        Play fall twice as fast, so a broken robot also gets bored quicker. Every other multiplier
        in the file is one.
        """
        multiplier = 1.0
        for other, need in self.needs.items():
            if other != name:
                multiplier *= need.multiplier_for(name)
        return multiplier

    def apply_action(self, action_id: str, now: Optional[float] = None) -> bool:
        """
        Apply what one action is worth, and say whether it counted.

        Returns False for an action the configuration does not name, or one still inside its own
        cooldown.
        """
        now = time.perf_counter() if now is None else now
        action = self.actions.get(action_id)
        if action is None:
            logger.warning("No need action named %s.", action_id)
            return False
        if not action.is_ready(now):
            return False
        action.last_applied_time = now
        for name, delta in action.sample().items():
            need = self.needs.get(name)
            if need is not None:
                need.add(delta, now)
        return True


def _read_rates(data: Dict, key: str) -> List[DecayRate]:
    return [DecayRate(threshold=entry["Threshold"], per_minute=entry["DecayPerMinute"])
            for entry in data.get(key, [])]


def _read_modifiers(data: Dict, key: str) -> List[DecayModifier]:
    out = []
    for entry in data.get(key, []):
        multipliers = {affected["OtherNeedID"]: float(affected["Multiplier"])
                       for affected in entry.get("OtherNeedsAffected", [])}
        out.append(DecayModifier(threshold=entry["Threshold"], multipliers=multipliers))
    return out


def load_needs(resource_dir: str, now: Optional[float] = None) -> Needs:
    """ Load the needs and everything that changes them. """

    start_time = time.perf_counter()
    config_dir = os.path.join(resource_dir, 'cozmo_resources', 'config', 'engine')

    config = load_json_file(os.path.join(config_dir, 'needs_config.json'))
    decay = load_json_file(os.path.join(config_dir, 'needs_decay_config.json'))
    rates = decay.get('DecayRates', {})
    modifiers = decay.get('DecayModifiers', {})

    minimum = float(config.get('MinimumNeedLevel', 0.0))
    maximum = float(config.get('MaximumNeedLevel', 1.0))

    needs = {}
    for name in NEEDS:
        needs[name] = Need(
            name=name,
            level=float(config.get('InitialNeedLevel' + name, maximum)),
            minimum=minimum,
            maximum=maximum,
            brackets={bracket: float(config['BracketLevel' + name + bracket])
                      for bracket in BRACKETS if 'BracketLevel' + name + bracket in config},
            decay_rates=_read_rates(rates, 'ConnectedDecayRates' + name),
            decay_modifiers=_read_modifiers(modifiers, 'ConnectedDecayModifiers' + name),
            fullness_cooldown=float(config.get('FullnessDecayCooldown' + name, 0.0)),
            now=now)

    actions = {}
    action_config = load_json_file(os.path.join(config_dir, 'needs_action_config.json'))
    for entry in action_config.get('actionDeltas', []):
        action = NeedAction.from_json(entry)
        actions[action.action_id] = action

    result = Needs(needs=needs, actions=actions,
                   decay_period=float(config.get('DecayPeriodSeconds', 60.0)))

    logger.debug("Loaded %s needs and %s need actions in %.02f s.",
                 len(needs), len(actions), time.perf_counter() - start_time)
    return result
