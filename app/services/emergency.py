import re
from dataclasses import dataclass

from app.schemas import Urgency


@dataclass(frozen=True)
class EmergencyDetection:
    urgency: Urgency
    signals: list[str]


_CRITICAL_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("fire", re.compile(r"화재|불이\s*(?:났|붙|번지)|연기가\s*(?:심|가득)")),
    ("gas_leak", re.compile(r"가스.{0,8}(?:누출|샘|새는|냄새)")),
    ("collapse", re.compile(r"붕괴|무너(?:졌|질|지고)|건물이\s*기울")),
    ("explosion", re.compile(r"폭발|폭발물")),
    ("electric_shock", re.compile(r"감전|전선.{0,8}(?:끊|노출|불꽃)")),
    ("person_in_danger", re.compile(r"사람.{0,10}(?:쓰러|갇혀|매몰|다쳤|의식이\s*없)")),
    ("sinkhole", re.compile(r"싱크홀|땅이\s*(?:꺼졌|꺼지|갈라졌)")),
)

_HIGH_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("flooding", re.compile(r"침수|물이\s*(?:차오|넘쳐)|하천.{0,8}범람")),
    ("fallen_tree", re.compile(r"나무.{0,8}(?:쓰러|넘어|도로를\s*막)")),
    ("open_manhole", re.compile(r"맨홀.{0,8}(?:열려|뚜껑이\s*없|빠졌)")),
    ("traffic_hazard", re.compile(r"도로.{0,8}(?:통행\s*불가|막고|위험)|신호등.{0,8}(?:꺼|고장)")),
)


def detect_emergency(text: str) -> EmergencyDetection:
    critical = [label for label, pattern in _CRITICAL_PATTERNS if pattern.search(text)]
    if critical:
        return EmergencyDetection(urgency=Urgency.CRITICAL, signals=critical)

    high = [label for label, pattern in _HIGH_PATTERNS if pattern.search(text)]
    if high:
        return EmergencyDetection(urgency=Urgency.HIGH, signals=high)

    return EmergencyDetection(urgency=Urgency.NORMAL, signals=[])
