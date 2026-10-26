# BankGuard

**Automatische Datenqualitätsprüfung für Banktransaktionen auf AWS.**
BankGuard prüft Transaktionsdateien aus mehreren Systemen, sortiert fehlerhafte Datensätze mit Begründung aus und stellt nur geprüfte Daten für Auswertungen bereit.

IHK-Projekt, Cloud Data Engineering Specialist (Prüfung am 10. November 2026). Alle Daten sind synthetisch; es werden keine echten Kundendaten verwendet.

## Inhalt

1. [Das Problem](#das-problem)
2. [Die Lösung](#die-lösung)
3. [Prüfregeln](#prüfregeln)
4. [Projektstand](#projektstand)
5. [Schnellstart](#schnellstart)
6. [Ergebnisse](#ergebnisse)
7. [Auf AWS bereitstellen](#auf-aws-bereitstellen)
8. [Projektstruktur](#projektstruktur)
9. [Tests](#tests)
10. [Bekannte Grenzen](#bekannte-grenzen)
11. [Hilfsmittel](#hilfsmittel) · [Weitere Dokumente](#weitere-dokumente)

## Das Problem

Banken bekommen Transaktionsdateien aus mehreren Systemen. Darin stehen fehlende Werte, doppelte IDs, falsche Datumsangaben, ungültige Beträge und Verweise auf Konten, die es nicht gibt. Ungeprüft verfälschen diese Daten Berichte und verursachen manuelle Nacharbeit.

**Projektfrage:** Wie kann eine Bank fehlerhafte Transaktionsdaten automatisch erkennen und isolieren, bevor sie in eine Analyseplattform gelangen?

## Die Lösung

BankGuard liest drei Dateien (Kunden, Konten, Transaktionen), prüft jeden Datensatz und trennt das Ergebnis:

- **Gültige Datensätze** kommen als nach Datum aufgeteiltes Parquet in den Bereich `curated/` und sind per SQL abfragbar (Athena).
- **Fehlerhafte Datensätze** kommen mit **allen** Ablehnungsgründen in den Bereich `rejected/`. Kein Datensatz wird still verworfen.
- Die **Originaldateien** bleiben unverändert in `raw/`.

```text
customers.csv   accounts.csv   transactions.csv
                     |
                     v
              S3  raw/              Originaldateien
                     |
                     v
          AWS Glue (Python-Prüfung)     src/validate.py
                     |
          +----------+----------+
          v                     v
   S3 curated/ (Parquet)   S3 rejected/ (JSON + Gründe)
   nach Datum aufgeteilt
          |
          v
   Glue Data Catalog  ->  Athena (SQL-Berichte)
```

Die Prüfung läuft in dieser Reihenfolge: erst Kunden, dann Konten (nur gegen gültige Kunden), dann Transaktionen (nur gegen gültige Konten). Dieselbe Datei `src/validate.py` wird von den Tests und vom Glue-Job benutzt, sodass die Tests den Code prüfen, der in der Cloud läuft.

Jede Transaktion nennt ihr Herkunftssystem (`CORE_BANKING`, `CARDS`, `ONLINE_BANKING`). Ergebnisse und Nacharbeit werden pro System ausgewiesen.

## Prüfregeln

Ein Datensatz kann mehrere Fehler haben; alle werden in `error_reasons` gespeichert.

| Regel | Fehlercodes |
| --- | --- |
| Pflichtfelder vorhanden (leer und `NULL` zählen als fehlend) | `MISSING_<FELD>`, z. B. `MISSING_AMOUNT` |
| Eindeutige IDs: Die Zeile mit der **niedrigsten Zeilennummer** bleibt, spätere werden abgelehnt | `DUPLICATE_TRANSACTION_ID`, `DUPLICATE_ACCOUNT_ID`, `DUPLICATE_CUSTOMER_ID`, `DUPLICATE_CONFLICTING_CONTENT` |
| Betrag ist eine Zahl, Datum im Format `JJJJ-MM-TT` | `INVALID_AMOUNT`, `INVALID_DATE` |
| Währung `EUR` oder `USD`, bekannte Transaktionsart und Herkunftssystem | `INVALID_CURRENCY`, `INVALID_TRANSACTION_TYPE`, `INVALID_SOURCE_SYSTEM` |
| Betrag positiv; negativ nur bei `REFUND` und `REVERSAL`; null wird abgelehnt | `AMOUNT_NOT_ALLOWED_FOR_TYPE` |
| Transaktion verweist auf gültiges Konto, Konto auf gültigen Kunden | `ACCOUNT_NOT_FOUND`, `CUSTOMER_NOT_FOUND` |
| Kontostatus `Active` oder `Closed` | `INVALID_STATUS` |

Zusätzlich muss bei jedem Lauf gelten: **Eingang = akzeptiert + abgelehnt**, auch für jedes Herkunftssystem einzeln.

Beispiel eines abgelehnten Datensatzes:

```json
{"transaction_id": "T0000123", "account_id": "A90012", "amount": "200.00",
 "source_system": "ONLINE_BANKING", "error_reasons": ["ACCOUNT_NOT_FOUND"],
 "line": 124, "run_id": "2026-10-28T10-00-00Z", "source_file": "transactions.csv"}
```

**Nacharbeits-Bericht** (`rework_report.csv`): pro System und Fehlergrund die Anzahl betroffener Datensätze und bis zu fünf Beispiel-IDs. Damit weiß jedes Quellsystem, was es korrigieren muss.

## Projektstand

| Teil | Stand |
| --- | --- |
| Testdaten-Generator, Prüfregeln, Verarbeitung, Nacharbeits-Bericht, 36 Tests | **Fertig und lokal ausgeführt** |
| Terraform, Glue-Job, Athena-Tabellen, SQL | Geschrieben, **noch nicht auf AWS ausgeführt** |
| Überwachung (CloudWatch, Alarm per E-Mail), Verschlüsselung, Start bei neuen Dateien, GitHub Actions, Airflow | Geschrieben, **noch nicht ausgeführt** |
| Ergebnisse, Laufzeiten und Screenshots von AWS | Offen |

Die Tests liefen bisher mit einem einfachen eigenen Testläufer. Bitte `pytest -q` selbst ausführen. Es wird nichts als AWS-Ergebnis ausgegeben, was nicht gemessen wurde.

## Schnellstart

Ohne AWS, mit Python 3.

**Beispiel mit 11 Zeilen:** fünf gültige und sechs abgelehnte Datensätze, je einer pro Fehlerart.

```bash
python -m src.transform examples output_example
cat output_example/rework_report.csv
```

**Alles mit 50.000 Transaktionen:**

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python -m src.generate_data           # erzeugt data/*.csv und data/injected_errors.json
python -m src.transform data output   # prüft, schreibt output/ und zeigt die Kennzahlen
pytest -q
```

Optionen des Generators: `--transactions N`, `--seed N`, `--out ORDNER`. Er speichert, welche Fehler eingebaut wurden. Daran messen die Tests, ob die Prüfung alle findet.

Mit Docker: `docker build -t bankguard . && docker run --rm bankguard` (noch nicht gebaut oder ausgeführt).

## Ergebnisse

Lokaler Lauf mit 51.000 Transaktionen (Startwert 42):

| Kennzahl | Wert |
| --- | --- |
| Eingang | 51.000 (50.000 + 1.000 eingespielte Dubletten) |
| Akzeptiert / abgelehnt | 47.500 / 3.500 |
| Anteil fehlerfrei | 93,14 % |
| Eingespielte Fehler | 3.750 |
| Gefunden / übersehen / falsch gemeldet | 3.750 / 0 / 0 |
| Dauer der Prüfung | etwa 0,6 Sekunden |

Das zeigt, dass die Regeln jeden Fehler finden, den der Generator erzeugt. Es beweist nicht, dass sie jeden Fehler in echten Daten finden. Die Fehlerraten pro Herkunftssystem sind ähnlich (92,7 bis 93,3 %), weil die Fehler gleichmäßig eingespielt werden; das sagt nichts über echte Systeme. **Ergebnisse von AWS gibt es noch nicht.**

## Auf AWS bereitstellen

*Nicht verifiziert: Dieser Weg wurde noch nicht ausgeführt, kleine Korrekturen beim ersten Lauf sind zu erwarten.*

Voraussetzungen: AWS-Konto, konfigurierte AWS CLI, Terraform, Rechte für S3, Glue, IAM, Athena, SNS, EventBridge und Budgets.

```bash
cd terraform
terraform init
terraform fmt
terraform validate
terraform apply -var="alert_email=du@example.com"   # Abo-Mail von AWS bestätigen
BUCKET=$(terraform output -raw bucket_name)
cd ..
for f in customers accounts transactions; do aws s3 cp data/$f.csv "s3://$BUCKET/raw/$f.csv"; done
```

Der Upload der drei Dateien startet den Glue-Job automatisch (Lambda-Trigger). Danach die Abfragen aus `sql/` in der Athena-Workgroup `bankguard` ausführen.

Aufräumen: `terraform destroy`. Bucket und Athena-Workgroup werden mit allen Inhalten gelöscht (`force_destroy = true`). Das ist für diese Testdaten gewollt und darf **nicht** für Projekte mit echten Daten übernommen werden.

Optional und standardmäßig **aus**: Amazon MWAA (`enable_mwaa=true`). Es wird laufend berechnet, solange es existiert. Anleitung: `docs/operations.md`, Abschnitt 6.

## Projektstruktur

```text
src/            generate_data.py · validate.py · transform.py
glue/           banking_etl.py (Glue-Job)
lambda_fn/      start_glue_job.py (startet den Job, wenn die drei Dateien da sind)
terraform/      Infrastruktur: s3 · kms · iam · glue · lambda · monitoring · github_oidc · mwaa
sql/            daily_transactions · country_analysis · data_quality · rework_report · compare_csv_vs_parquet
airflow/dags/   bankguard_pipeline.py (optional)
scripts/        athena_scan_compare.py
examples/       Beispiel mit 11 Zeilen
tests/          test_validation · test_transform · test_problem_statement · test_lambda_trigger
docs/           Datenmodell, Betrieb, Projektplan, Werkzeug-Übersicht
.github/workflows/  ci.yml · deploy.yml
Dockerfile · requirements.txt · pytest.ini
```

## Tests

36 automatische Tests. `tests/test_problem_statement.py` hat einen Test pro Satz der Problembeschreibung, unter anderem:

- Das Beispiel in `examples/` liefert genau das erwartete Ergebnis.
- **Kein Datensatz geht verloren:** Jede Eingabezeile steht genau einmal in „gültig" oder „abgelehnt".
- **Berichte sehen nur Gültiges:** keine doppelten IDs, keine unbekannten Konten, keine ungültigen Beträge oder Daten.
- Jeder abgelehnte Datensatz hat mindestens einen Grund.
- Jedes Herkunftssystem geht für sich auf.
- Ein zweiter Lauf mit denselben Daten liefert dieselben Ergebnisse (nur `run_id` und Zeitstempel unterscheiden sich).


## Weitere Dokumente

- `docs/data-model.md`: Datenmodell mit Diagramm, Bereiche in S3, Tabellen, Fehlercodes
- `docs/operations.md`: Automatisierung, Zugriffsrechte, Überwachung, MWAA
- `docs/ihk-plan.md`: Zeitplan und Präsentation
- `docs/tool-map.md`: Werkzeugliste mit ehrlichem Stand
- `docs/cost-estimate.md`: Vorlage für die Kostenschätzung
- `docs/overview-en.md`: technische Übersicht auf Englisch

*BankGuard ist eine Lern- und Demonstrationsarbeit mit synthetischen Daten, kein Produktivsystem.*
