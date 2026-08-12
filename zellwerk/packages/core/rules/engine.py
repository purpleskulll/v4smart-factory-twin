"""Edge-Rule-Engine — der deterministische <1s-Pfad (SPEC §8.3).

Drei Eigenschaften sind hier nicht verhandelbar:

1. **Kein LLM.** Alles unter einer Sekunde läuft über feste Regeln. Ein
   Sprachmodell im Sicherheitspfad wäre weder schnell noch reproduzierbar.
2. **Kein Datenbank-Roundtrip.** Ausgewertet wird direkt im MQTT-Strom. Der
   Zustand, den eine Regel braucht (etwa „seit 3 s über dem Grenzwert"), liegt
   im Speicher.
3. **Auswertung ohne IO testbar.** Diese Datei kennt weder MQTT noch Broker —
   `main.py` bindet sie an. Nur so lässt sich die geforderte Latenz überhaupt
   sauber messen.

Die KI darf Regeln VORSCHLAGEN (als Diff auf rules.yaml), niemals selbst
einführen. Diese Datei wird deshalb nie von einem Agenten geschrieben.
"""

from __future__ import annotations

import operator
import re
from dataclasses import dataclass
from typing import Any

import yaml

OPERATOREN = {
    ">": operator.gt, ">=": operator.ge,
    "<": operator.lt, "<=": operator.le,
    "==": operator.eq, "!=": operator.ne,
}


@dataclass
class Condition:
    topic: str            # Topic-Muster, `...` und `*` als Platzhalter
    op: str
    value: float
    for_s: float = 0.0    # wie lange die Bedingung halten muss
    quality: str | None = None  # optional auf Qualität filtern

    def __post_init__(self) -> None:
        if self.op not in OPERATOREN:
            raise ValueError(f"unbekannter Operator: {self.op}")
        self._regex = re.compile(
            "^" + re.escape(self.topic).replace(r"\.\.\.", ".*").replace(r"\*", "[^/]+") + "$"
        )

    def matches_topic(self, topic: str) -> bool:
        return bool(self._regex.match(topic))

    def holds(self, value: Any) -> bool:
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            return False
        return OPERATOREN[self.op](value, self.value)


@dataclass
class Action:
    kind: str             # "publish" | "event"
    topic: str | None = None
    payload: dict | None = None
    severity: str | None = None
    code: str | None = None


@dataclass
class Rule:
    id: str
    when: Condition
    then: list[Action]
    note: str = ""
    cooldown_s: float = 30.0


@dataclass
class Trigger:
    """Was eine Regel ausgelöst hat — inklusive Beleg für das Audit-Log."""

    rule_id: str
    topic: str
    value: float
    actions: list[Action]
    held_for_s: float


@dataclass
class _State:
    seit: float | None = None
    zuletzt_ausgeloest: float | None = None


class RuleEngine:
    """Wertet Regeln gegen einen Strom von (topic, value, ts) aus."""

    def __init__(self, rules: list[Rule]) -> None:
        self.rules = rules
        self._state: dict[tuple[str, str], _State] = {}

    @classmethod
    def from_yaml(cls, path: str) -> RuleEngine:
        with open(path, encoding="utf-8") as handle:
            raw = yaml.safe_load(handle) or []
        return cls([cls._parse_rule(entry) for entry in raw])

    @staticmethod
    def _parse_rule(entry: dict) -> Rule:
        when = entry["when"]
        actions: list[Action] = []
        for step in entry.get("then", []):
            if "publish" in step:
                pub = step["publish"]
                actions.append(Action("publish", topic=pub["topic"],
                                      payload=pub.get("payload", {})))
            elif "event" in step:
                ev = step["event"]
                actions.append(Action("event", severity=ev.get("severity", "warn"),
                                      code=ev.get("code", "UNBEKANNT")))
        return Rule(
            id=entry["id"],
            when=Condition(topic=when["topic"], op=when["op"], value=float(when["value"]),
                           for_s=float(when.get("for_s", 0.0)), quality=when.get("quality")),
            then=actions,
            note=entry.get("note", ""),
            cooldown_s=float(entry.get("cooldown_s", 30.0)),
        )

    def evaluate(self, topic: str, value: Any, ts: float, quality: str = "good") -> list[Trigger]:
        """Ein Wert kommt herein — welche Regeln lösen aus?

        Wird für JEDEN eingehenden Wert gerufen; entsprechend billig gehalten.
        """
        ausgeloest: list[Trigger] = []

        for rule in self.rules:
            if not rule.when.matches_topic(topic):
                continue
            if rule.when.quality is not None and quality != rule.when.quality:
                continue

            key = (rule.id, topic)
            state = self._state.setdefault(key, _State())

            if not rule.when.holds(value):
                state.seit = None  # Bedingung gebrochen — Haltezeit neu starten
                continue

            if state.seit is None:
                state.seit = ts
            gehalten = ts - state.seit
            if gehalten < rule.when.for_s:
                continue

            if (state.zuletzt_ausgeloest is not None
                    and ts - state.zuletzt_ausgeloest < rule.cooldown_s):
                continue

            state.zuletzt_ausgeloest = ts
            ausgeloest.append(Trigger(rule.id, topic, float(value), rule.then, gehalten))

        return ausgeloest

    def resolve_actions(self, trigger: Trigger) -> list[Action]:
        """Setzt Platzhalter in Topics/Payloads aus dem auslösenden Topic ein.

        `{channel}` wird aus einem Topic wie `.../ch3_temp_c` gezogen — sonst
        könnte eine Regel für acht Kanäle nicht mit einem Eintrag auskommen.
        """
        kanal = None
        if (treffer := re.search(r"/ch(\d+)_", trigger.topic)):
            kanal = treffer.group(1)

        aufgeloest: list[Action] = []
        for action in trigger.actions:
            topic = action.topic
            payload = dict(action.payload or {})
            if kanal is not None:
                if topic:
                    topic = topic.replace("{channel}", kanal)
                payload = {
                    k: (v.replace("{channel}", kanal) if isinstance(v, str) else v)
                    for k, v in payload.items()
                }
                payload.setdefault("channel", int(kanal))
            aufgeloest.append(Action(action.kind, topic, payload, action.severity, action.code))
        return aufgeloest
