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


# ---------- Ort: Berlin oder remote in Deutschland ----------

_BERLIN = re.compile(r"berlin", re.I)
_REMOTE = re.compile(r"remote|home ?office|mobiles arbeiten|work from home|anywhere|fully distributed", re.I)
_DE = re.compile(r"germany|deutschland|\bde\b|\bdach\b|\bger\b|berlin|münchen|munich|hamburg|köln|cologne|frankfurt", re.I)
# Wörter, die kein bestimmtes Land meinen ("Remote", "Europe", ...). Bleibt nach dem Entfernen nichts übrig,
# ist die Stelle ortsunabhängig und kommt in Frage.
_GENERIC = re.compile(
    r"remote|home ?office|mobiles arbeiten|work from home|anywhere|worldwide|global|fully distributed|"
    r"europe|europa|\bemea\b|\beu\b|\bcet\b|time ?zones?|hybrid|flexible|office|[|(),/\-–:;]",
    re.I,
)


# Andere Schreibweisen für Städte, die man in companies.yaml unter "orte" angeben kann
CITY_ALIASES = {
    "münchen": "münchen|munich|muenchen",
    "köln": "köln|cologne|koeln",
    "frankfurt": "frankfurt",
    "hamburg": "hamburg",
    "düsseldorf": "düsseldorf|duesseldorf|dusseldorf",
    "wien": "wien|vienna",
    "zürich": "zürich|zurich|zuerich",
}


def place(locations, remote_flag=False, extra_cities=None):
    """Bewertet die Ortsangaben einer Stelle.

    Rückgabe: (location_text, remote, ok)
    ok = Berlin, oder remote und (Deutschland/DACH genannt oder gar kein bestimmtes Land, z. B. "Remote Europe").
    extra_cities: weitere erlaubte Städte für einzelne Firmen (companies.yaml, Feld "orte").
    """
    locs = [l.strip() for l in locations if l and l.strip()]
    text = " | ".join(dict.fromkeys(locs))
    remote = bool(remote_flag or _REMOTE.search(text))
    if any(_BERLIN.search(l) for l in locs):
        return "Berlin", remote, True
    for city in extra_cities or []:
        c = str(city).strip().lower()
        if c == "berlin" or not c:
            continue
        rx = re.compile(CITY_ALIASES.get(c, re.escape(c)), re.I)
        if any(rx.search(l) for l in locs):
            return str(city).strip(), remote, True
    if remote:
        if _DE.search(text) or not _GENERIC.sub(" ", text).strip():
            return text or "Remote", True, True
    return text, remote, False


# ---------- Sprache: Fabian spricht Deutsch und Englisch ----------

_LANGS = (
    r"french|spanish|italian|dutch|flemish|polish|portuguese|swedish|danish|norwegian|finnish|czech|slovak|"
    r"slovenian|hungarian|romanian|turkish|greek|arabic|russian|ukrainian|japanese|chinese|mandarin|cantonese|"
    r"korean|hebrew|croatian|serbian|bulgarian|"
    r"französisch|spanisch|italienisch|niederländisch|polnisch|portugiesisch|schwedisch|dänisch|norwegisch|"
    r"finnisch|tschechisch|slowakisch|slowenisch|ungarisch|rumänisch|türkisch|griechisch|arabisch|russisch|"
    r"ukrainisch|japanisch|chinesisch|koreanisch"
)
# Fremdsprachige Märkte im Titel, z. B. "Account Executive - France"
_MARKETS = (
    r"france|italy|spain|iberia|netherlands|benelux|nordics?|poland|turkey|portugal|czechia|latam|"
    r"japan|korea|brazil|mexico|frankreich|italien|spanien|niederlande|polen|türkei|"
    r"africa|afrika|apac|asia|asien|americas|north america|nordamerika|middle east|mena"
)
OTHER_LANGUAGE = re.compile(
    rf"\b({_LANGS})[\s-]*(speaking|speaker|sprachig|native)|"
    rf"\b(fluent|native|verhandlungssicher(es)?)\s+({_LANGS})\b|"
    rf"\b({_MARKETS})\b|\b({_LANGS})\s+market\b",
    re.I,
)


# Länderkürzel am Titelende, z. B. "Account Manager ES" (Groß-/Kleinschreibung beachten)
_COUNTRY_SUFFIX = re.compile(r"[\s(-](ES|FR|IT|NL|PL|PT|TR|SE|DK|NO|FI|CZ|BR|MX|JP|NA|UK|UKI|AMER|NAMER|APAC|LATAM|US|USA)\)?\s*$")


def needs_other_language(title):
    """True, wenn der Titel eine andere Sprache als Deutsch/Englisch oder einen fremdsprachigen Markt nennt."""
    t = title or ""
    return bool(OTHER_LANGUAGE.search(t) or _COUNTRY_SUFFIX.search(t))
