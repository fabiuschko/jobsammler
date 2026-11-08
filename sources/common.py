"""Gemeinsame Hilfsfunktionen für alle Quellen."""

import html
import re
import time
import warnings
from pathlib import Path

warnings.filterwarnings("ignore", message=".*OpenSSL.*")  # harmlos bei Apples Python 3.9

import requests  # noqa: E402
import yaml  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
USER_AGENT = "jobsammler/0.1 (+https://github.com/fabiuschko/jobsammler)"


class PoliteSession:
    """HTTP-Zugriff mit Pause zwischen Anfragen, Wiederholung bei Fehlern und Aufrufzähler."""

    def __init__(self, pause=0.4, timeout=30, retries=2):
        self.s = requests.Session()
        self.s.headers["User-Agent"] = USER_AGENT
        self.pause = pause
        self.timeout = timeout
        self.retries = retries
        self.calls = 0
        self._last = 0.0

    def get(self, url, **kw):
        kw.setdefault("timeout", self.timeout)
        last_exc = None
        for attempt in range(self.retries + 1):
            wait = self.pause - (time.monotonic() - self._last)
            if wait > 0:
                time.sleep(wait)
            self._last = time.monotonic()
            self.calls += 1
            try:
                r = self.s.get(url, **kw)
                if r.status_code in (429, 500, 502, 503, 504) and attempt < self.retries:
                    time.sleep(2 * (attempt + 1))
                    continue
                return r
            except requests.RequestException as exc:
                last_exc = exc
                time.sleep(2 * (attempt + 1))
        raise last_exc


def load_yaml(rel_path):
    with open(ROOT / rel_path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def to_text(raw, limit=2000):
    """HTML oder Markdown grob in Klartext umwandeln und kürzen."""
    if not raw:
        return ""
    t = re.sub(r"(?i)<br\s*/?>|</p>|</li>|</h\d>", "\n", raw)
    t = re.sub(r"<[^>]+>", " ", t)
    t = html.unescape(t)
    t = re.sub(r"[#*_`]{1,}", "", t)  # Markdown-Zeichen
    t = re.sub(r"[ \t\xa0]+", " ", t)
    t = re.sub(r"\s*\n\s*", "\n", t).strip()
    return t[:limit]


# ---------- Rollen-Erkennung und grobe Filter (für alle Quellen gleich) ----------

_GENDER = re.compile(r"\((?:[mwfdx]\s*[/|,]\s*)+[mwfdx]\)|\b[mwfdx]/[mwfdx]/[mwfdx]\b|\*in\b", re.I)


def normalize_title(title):
    t = _GENDER.sub(" ", title or "")
    t = t.lower().replace("’", "'")
    t = re.sub(r"[^a-z0-9äöüß&' ]+", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def _words(s):
    return normalize_title(s).replace("-", " ").split()


def _word_hit(t, w):
    """Titelwort t passt zu Suchwort w: gleich, weibliche Form, oder (bei langen Wörtern) Wortanfang.
    'account' passt also nicht auf 'accounting', 'bestandskunden' aber auf 'bestandskundenbetreuer'."""
    return t == w or t in (w + "in", w + "innen", w + "s") or (len(w) >= 9 and t.startswith(w))


class RoleMatcher:
    """Ordnet einen Stellentitel einer Rolle aus roles.yaml zu.

    Ein Suchbegriff passt, wenn alle seine Wörter im Titel vorkommen
    (oder als Wortanfang, z. B. 'Bestandskunden' in 'Bestandskundenbetreuer').
    Bei mehreren Treffern gewinnt der längste Suchbegriff.
    """

    def __init__(self, roles):
        self.terms = []
        for key, spec in roles.items():
            for term in spec.get("terms", []):
                self.terms.append((key, term, _words(term)))
        self.terms.sort(key=lambda x: -len(" ".join(x[2])))

    def guess(self, title):
        tw = _words(title)
        compact = "".join(tw)
        for key, _term, words in self.terms:
            if all(any(_word_hit(t, w) for t in tw) for w in words):
                return key
            if len(words) > 1 and "".join(words) in compact:  # 'Partnermanager', 'Accountmanager'
                return key
        return ""


EXCLUDE_TITLE = re.compile(
    r"praktik|intern\b|internship|werkstudent|working student|studentische|ausbildung|azubi|"
    r"trainee|minijob|aushilfe|duales studium|dual student|abschlussarbeit|thesis",
    re.I,
)
SENIOR_TITLE = re.compile(r"\b(senior|sr\.?|lead|head|director|principal|vp|vice president)\b", re.I)


def is_excluded_title(title):
    return bool(EXCLUDE_TITLE.search(title or ""))


def senior_flag(title):
    return bool(SENIOR_TITLE.search(title or ""))
