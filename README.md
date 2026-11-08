# Jobsammler

Sammelt jeden Morgen offene Stellen aus verlässlichen, strukturierten Quellen und schreibt sie nach
[`data/jobs.json`](data/jobs.json). Läuft automatisch als GitHub Action (täglich 04:30 UTC), auch wenn kein Rechner an ist.

**Regel:** Eine Stelle ist offen, wenn sie heute in der Quelle steht. Fehlt sie, ist sie geschlossen.
Geschlossene Stellen bleiben 60 Tage in der Datei.

Rohdatei zum Weiterverarbeiten: `https://raw.githubusercontent.com/fabiuschko/jobsammler/main/data/jobs.json`
Protokoll des letzten Laufs (Zahlen pro Quelle, Fehler): [`data/run.json`](data/run.json)

## Quellen

| Quelle | Art | `sourceTier` |
|---|---|---|
| Bewerbungssysteme der Zielfirmen (Greenhouse, Lever, Ashby, Personio, Workable, SmartRecruiters) | offizielle öffentliche Stellen-Schnittstellen | `kern` |
| [Arbeitnow](https://www.arbeitnow.com) | öffentliche Job-Board-API, viele Start-ups | `kern` |
| Jobsuche der Bundesagentur für Arbeit | öffentlich genutzte Schnittstelle ([bundesAPI](https://github.com/bundesAPI/jobsuche-api)) | `breit` |

VC-Jobbörsen (Getro) werden nicht ausgelesen, weil deren Nutzungsbedingungen das verbieten (siehe `config/vc_boards.yaml`).

## Was du anpassen kannst

Alle Einstellungen stehen im Ordner `config/`. Ändern geht direkt auf github.com: Datei öffnen, auf den Stift klicken,
ändern und mit **Commit changes** speichern. Ab dem nächsten Lauf gilt die Änderung.

- **`roles.yaml`**: Rollen und Suchbegriffe. Neue Suchbegriffe einfach ergänzen. Bei einer **neuen Rolle**
  (neues Kürzel) auch dem Bewerbungs-Cockpit in claude.ai Bescheid sagen, damit es die Rolle kennt.
- **`companies.yaml`**: Zielfirmen und ihr Bewerbungssystem.
- **`exclude.yaml`**: Firmen, die nie auftauchen sollen (z. B. Axel Springer, Immobilienfirmen, Personalvermittler).
- **`vc_boards.yaml`**: Status der VC-Jobbörsen.

### Eine Firma hinzufügen

Am einfachsten: in Claude Code sagen „Nimm Firma X auf“. Oder selbst im Projektordner:

```bash
.venv/bin/python scripts/detect_ats.py "Firmenname" --add
```

Das Skript sucht die Karriereseite, erkennt das Bewerbungssystem, prüft es mit einem echten Abruf und trägt die Firma ein.
Wird nichts erkannt, landet sie unter `nicht_erkannt`. Für einzelne Firmen kann man weitere Städte erlauben,
z. B. `orte: [Berlin, München]` (sonst gilt: Berlin oder remote in Deutschland).

### Neue Firmen vorschlagen lassen

```bash
.venv/bin/python scripts/suggest_companies.py
```

Sammelt Start-ups, die bei Arbeitnow mit passenden Stellen auftauchen und noch nicht auf der Liste stehen, und schreibt sie
als `vorschlaege` in `companies.yaml`. Übernehmen mit `--uebernehmen "Firma A, Firma B"`.

## Filter (grob, die Feinbewertung macht Claude)

- Ort: Berlin oder remote in Deutschland
- Raus: Praktikum, Werkstudent, Ausbildung, Minijob, Teilzeit; Stellen, deren Titel eine andere Sprache als Deutsch/Englisch
  oder einen ausländischen Markt verlangt; bei der Arbeitsagentur außerdem Zeitarbeit, Personalvermittlung, Außendienst, Callcenter
- Markiert statt rausgeworfen: Senior, Lead, Head, Director, Principal (`seniorFlag: true`)

## Lokal ausführen

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python collect.py
```
