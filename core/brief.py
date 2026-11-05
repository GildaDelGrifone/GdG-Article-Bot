import re
from typing import NamedTuple, Optional

_MARKER = re.compile(r"^[ \t]*(copertina|cover|logo|ordine)[ \t]*:[ \t]*(\d+)[ \t]*(?:\n|$)", re.IGNORECASE | re.MULTILINE)


class Brief(NamedTuple):
    text: str
    cover: Optional[int]
    logo: Optional[int]
    order: Optional[int]


def parse_markers(text):
    """Separa dal brief le righe `copertina: N`, `logo: N` e `ordine: N`.

    Restituisce il brief senza quelle righe e i numeri trovati (l'ultimo vince); `cover` è un sinonimo di `copertina`.
    """
    found = {}
    for name, value in _MARKER.findall(text or ""):
        key = {"copertina": "cover", "cover": "cover", "logo": "logo", "ordine": "order"}[name.lower()]
        found[key] = int(value)
    clean = _MARKER.sub("", text or "")
    clean = re.sub(r"\n{3,}", "\n\n", clean).strip()
    return Brief(clean, found.get("cover"), found.get("logo"), found.get("order"))
