# BankGuard

**Automatische Datenqualitätsprüfung für Banktransaktionen auf AWS.**
BankGuard prüft Transaktionsdateien aus mehreren Systemen, sortiert fehlerhafte Datensätze mit Begründung aus und stellt nur geprüfte Daten für Auswertungen bereit.

IHK-Projekt, Cloud Data Engineering Specialist (Prüfung am 10. November 2026). Alle Daten sind synthetisch; es werden keine echten Kundendaten verwendet.

**Betriebsstatus nach dem Abbau am 04.11.2026:** Nach den erfolgreichen Tests wurden alle 73 Terraform-Ressourcen und drei zusätzliche Snapshot-Kataloge entfernt; der State ist leer. Der KMS-Schlüssel ist zur Löschung vorgemerkt. CloudWatch-Logs außerhalb des State und der gemeinsam genutzte GitHub-OIDC-Provider bleiben erhalten. [Abbau-Nachweis](docs/evidence/2026-11-04/terraform-destroy/summary.json). Die [AWS-Prüfung](docs/evidence/2026-11-04/aws-verification.json) und [MWAA-Läufe](docs/evidence/2026-11-04/airflow-runs.json) dokumentieren den Teststand vor dem Abbau.

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

- **Gültige Datensätze** kommen als Parquet in den Snapshot-Bereich `snapshots/<run-id>/curated/` und sind per SQL abfragbar (Athena). Transaktionen werden nach Datum partitioniert; Kunden und Konten werden ohne Datumspartitionierung gespeichert.
- **Fehlerhafte Datensätze** kommen mit **allen** Ablehnungsgründen in den Snapshot-Bereich `snapshots/<run-id>/rejected/`. Kein Datensatz wird still verworfen.
- Die **Originaldateien** liegen pro Datenpaket unverändert unter `raw/batches/<id>/`. Alte S3-Versionen werden gemäß Lifecycle nach 30 Tagen gelöscht.

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
   Snapshot curated/      Snapshot rejected/
          |
   published/current.json  -> vollständige Snapshot-Datenbank
   Transaktionen nach Datum partitioniert
          |
          v
   Glue Data Catalog  ->  Athena (SQL-Berichte)
```

Glue schreibt pro Lauf getrennte Ausgaben und eine eigene Katalogdatenbank. Erst nach vollständigem Erfolg schaltet `published/current.json` auf diesen Snapshot um. Eine Quittung unter `runs/<Glue-Run-ID>/metrics.json` bestätigt den konkreten Lauf. Fehlgeschlagene Vorbereitungen lassen den bisherigen veröffentlichten Datenbestand unverändert.

Die Prüfung läuft in dieser Reihenfolge: erst Kunden, dann Konten (nur gegen gültige Kunden), dann Transaktionen (nur gegen gültige Konten). Dieselbe Datei `src/validate.py` wird von den Tests und vom Glue-Job benutzt, sodass die Tests den Code prüfen, der in der Cloud läuft.

Jede Transaktion nennt ihr Herkunftssystem (`CORE_BANKING`, `CARDS`, `ONLINE_BANKING`). Ergebnisse und Nacharbeit werden pro System ausgewiesen.

## Prüfregeln

Ein Datensatz kann mehrere Fehler haben; alle werden in `error_reasons` gespeichert. Abweichende CSV-Zeilenlängen erhalten `INVALID_COLUMN_COUNT`; zusätzliche Werte bleiben erhalten. Ungültige Kopfzeilen und CSV-Syntaxfehler stoppen den Lauf ausdrücklich.

`customers.csv` unterstützt die optionalen Spalten `email` und `tel`. Alte Kundendateien ohne diese Spalten bleiben zulässig. Ausgefüllte Werte werden auf E-Mail- bzw. Telefonnummernformat geprüft; fehlende Kontaktwerte werden in akzeptierten Ausgaben als leere Zeichenketten gespeichert. Die genauen Formate stehen im [Datenmodell](docs/data-model.md).

| Regel | Fehlercodes |
| --- | --- |
| Pflichtfelder vorhanden (leer und `NULL` zählen als fehlend) | `MISSING_<FELD>`, z. B. `MISSING_AMOUNT` |
| IDs enthalten keine eingebetteten ASCII-/Unicode-Steuerzeichen (Kategorie `Cc`) | `INVALID_CUSTOMER_ID`, `INVALID_ACCOUNT_ID`, `INVALID_TRANSACTION_ID` |
| Eindeutige IDs: Die Zeile mit der **niedrigsten Zeilennummer** wird anhand ihrer eigenen Werte geprüft; spätere Vorkommen werden auch dann abgelehnt, wenn das Original ungültig ist. Abweichende Transaktionsinhalte erhalten zusätzlich `DUPLICATE_CONFLICTING_CONTENT` | `DUPLICATE_TRANSACTION_ID`, `DUPLICATE_ACCOUNT_ID`, `DUPLICATE_CUSTOMER_ID`, `DUPLICATE_CONFLICTING_CONTENT` |
| Betrag passt exakt in `DECIMAL(18,2)`, Datum im Format `JJJJ-MM-TT` | `INVALID_AMOUNT`, `INVALID_DATE` |
| Währung `EUR` oder `USD`, bekannte Transaktionsart und Herkunftssystem | `INVALID_CURRENCY`, `INVALID_TRANSACTION_TYPE`, `INVALID_SOURCE_SYSTEM` |
| Betrag positiv; negativ nur bei `REFUND` und `REVERSAL`; null wird abgelehnt | `AMOUNT_NOT_ALLOWED_FOR_TYPE` |
| Transaktion verweist auf gültiges Konto, Konto auf gültigen Kunden | `ACCOUNT_NOT_FOUND`, `CUSTOMER_NOT_FOUND` |
| Kontostatus `Active` oder `Closed` | `INVALID_STATUS` |
| Ausgefüllte Kundenkontakte erfüllen die Formatregeln | `INVALID_EMAIL`, `INVALID_TEL` |

Zusätzlich muss bei jedem Lauf gelten: **Eingang = akzeptiert + abgelehnt**, auch für jede Datei und jedes Herkunftssystem einzeln. Die obersten Zähler in `metrics.json` und `by_source_system` betreffen Transaktionen; `by_dataset` enthält Kunden, Konten und Transaktionen getrennt, `overall` den Gesamtstand. Vorhandene Erfolgsquoten müssen mit den Zählern übereinstimmen.

Auszug aus einem abgelehnten Datensatz:

```json
{"transaction_id": "T0000123", "account_id": "A90012", "amount": "200.00",
 "source_system": "ONLINE_BANKING", "error_reasons": ["ACCOUNT_NOT_FOUND"],
 "line": 124, "run_id": "2026-10-28T10-00-00Z", "source_file": "transactions.csv"}
```

**Nacharbeits-Bericht** (`rework_report.csv`): pro System und Fehlergrund die Anzahl betroffener Datensätze und bis zu fünf Beispiel-IDs. Damit weiß jedes Quellsystem, was es korrigieren muss.

Zusätzlich enthält `dataset_rework_report.csv` lokal die Fehler aller drei Dateien mit Datensatzart, Fehlergrund und Beispiel-IDs. Glue schreibt diesen Gesamtbericht nach `snapshots/<run-id>/dataset_rework_report.json`.

CloudWatch erhält getrennte Kennzahlen für jede Datei und den Gesamtstand, jeweils mit den Dimensionen `JobName` und `Dataset`. Kunden, Konten und Transaktionen haben eigene Qualitätsalarme, zusätzlich besteht ein Gesamtalarm. Leere Dateien liefern Zähler mit Wert null und keine Erfolgsquote.

## Projektstand

**Stand vom 04.11.2026:** 378 Tests lokal und im aktuellen Docker-Image bestanden. Der Stand wurde auf AWS bereitgestellt und geprüft; die Demo-Infrastruktur ist inzwischen abgebaut. Das Prüfskript bestätigt Kunden, Konten und Transaktionen mit zwölf Athena-Abfragen sowie dem CSV-/Parquet-Vergleich; zwei MWAA-Läufe waren erfolgreich. Die SNS-E-Mail-Subscription war beim Test bestätigt; die Zustellung der neuen Qualitätsalarme wurde noch nicht erneut getestet. Frühere CI- und Alarmnachweise behalten ihren angegebenen Versions- und Datumsbezug.

| Teil | Stand |
| --- | --- |
| Testdaten-Generator, Prüfregeln, Verarbeitung, Nacharbeits-Bericht, Regressionstests | **Fertig und lokal ausgeführt** |
| Terraform, Glue-Job, Athena-Tabellen, SQL | **Erneut bereitgestellt und am 04.11.2026 auf AWS geprüft** |
| CloudWatch, SNS, Verschlüsselung und automatischer Dateistart | Am 04.11.2026 bereitgestellt; automatischer Paketlauf erfolgreich. Alarmzustellung über EventBridge/SNS am 28.10.2026 geprüft; im neuen Deployment nicht erneut getestet |
| GitHub Actions (historische Nachweise) | [CI erfolgreich](https://github.com/gholipourmarya/BankGuard-AWS-Data-Engineering/actions/runs/40662802001); [OIDC-Deployment erfolgreich](https://github.com/gholipourmarya/BankGuard-AWS-Data-Engineering/actions/runs/40739499524) auf `feature/bankguard` |
| Airflow / MWAA | **Zwei erfolgreiche Läufe am 04.11.2026**, Airflow 3.3.1; [Nachweis](docs/evidence/2026-11-04/airflow-runs.json) |
| AWS-Nachweise | [Aktueller Abgleich aller drei Datensätze und zwölf Athena-Abfragen](docs/evidence/2026-11-04/aws-verification.json); [historische Messwerte](docs/evidence/2026-10-28/aws-verification.json) |

Die Tests wurden mit `python -m pytest -q` ausgeführt. Die aktuelle Anzahl steht in dessen Ausgabe. Es wird nichts als AWS-Ergebnis ausgegeben, was nicht gemessen wurde.

## Schnellstart

Ohne AWS, empfohlen mit Python 3.12 wie in CI und Docker. Alle Befehle aus dem Projektverzeichnis ausführen. Für die Verarbeitung des kleinen Beispiels reicht die Python-Standardbibliothek; für Tests und AWS-Skripte die unten genannten Abhängigkeiten installieren.

**Beispiel mit 11 Transaktionen:** fünf gültige und sechs abgelehnte Datensätze für sechs ausgewählte Fehlerarten. Die [Beispielerklärung](examples/README.md) nennt jede Fehlerzeile und die erwarteten Ausgaben.

```bash
python -m src.transform examples output_example
cat output_example/rework_report.csv
cat output_example/dataset_rework_report.csv
```

**Alles mit 50.000 Transaktionen:**

```bash
python -m venv .venv && source .venv/bin/activate
python -m pip install -r requirements.txt
python -m src.generate_data           # erzeugt data/*.csv und data/injected_errors.json
python -m src.transform data output   # prüft, schreibt output/ und zeigt die Kennzahlen
python -m pytest -q
```

Optionen des Generators: `--transactions N`, `--seed N`, `--out ORDNER`. Er speichert, welche Fehler eingebaut wurden. Daran messen die Tests, ob die Prüfung alle findet.

`requirements.txt` verwendet die festen Paketversionen aus `requirements.lock` für lokale Tests, CI und Docker. Der Docker-Build verwendet zusätzlich einen festen Digest des Python-Basisimages. Versionsänderungen werden bewusst zusammen mit den Tests geprüft. Die optionale Airflow-Umgebung hat eigene Constraints und wird getrennt installiert; siehe [Betrieb](docs/operations.md#6-optionales-airflow-und-mwaa).

Mit Docker: `docker build -t bankguard . && docker run --rm bankguard`. Am 04.11.2026 erfolgreich gebaut und ausgeführt: 378 Tests sowie die vollständige Datenverarbeitung. Das Image enthält das feste Basisimage und die Lockdatei; lokale Python-Sicherungen, Umgebungen und Terraform-Dateien mit lokalem Zustand sind ausgeschlossen.

## Ergebnisse

Lokaler Lauf mit 51.000 Transaktionen (Startwert 42):

| Kennzahl | Wert |
| --- | --- |
| Eingang | 51.000 (50.000 + 1.000 eingespielte Dubletten) |
| Akzeptiert / abgelehnt | 47.500 / 3.500 |
| Anteil fehlerfrei | 93,14 % |
| Eingespielte Fehler | 3.750 |
| Gefunden / übersehen / falsch gemeldet | 3.750 / 0 / 0 |
| Dauer der Prüfung | etwa 3,6 Sekunden in einem früheren lokalen Messlauf; versions- und umgebungsabhängig |

Die Tabellenwerte beziehen sich auf Transaktionen. Mit zusätzlich 2.500 gültigen Kunden und 2.500 gültigen Konten beträgt die Gesamtquote (`overall`) 93,75 %.

Das zeigt, dass die Regeln jeden Fehler finden, den der Generator erzeugt. Es beweist nicht, dass sie jeden Fehler in echten Daten finden. Die Erfolgsquoten pro Herkunftssystem sind ähnlich (92,7 bis 93,3 %), weil die Fehler gleichmäßig eingespielt werden; das sagt nichts über echte Systeme. **AWS-Lauf vom 28.10.2026:** ebenfalls 51.000 Eingang, 47.500 gültig und 3.500 abgelehnt; Glue-Ausführungszeit 135 Sekunden bei zwei G.1X-Workern. Athena bestätigt die Anzahlen. [Nachweise](docs/evidence/2026-10-28/aws-verification.json), [Formatvergleich und Kosten](docs/cost-estimate.md).

Historische Nachprüfung des ETL-Releases aus Commit `80af12b` am 28.10.2026: **AWS-Deployment und Integrationstests erfolgreich**. Das vollständige Paket enthält zusätzlich eine absichtlich ungültige Unicode-ID (`U+0085`): **51.001 Eingang = 47.500 akzeptiert + 3.501 abgelehnt**, Glue-Ausführungszeit **127 Sekunden**. Der MWAA-DAG bestand außerdem mit **1 Eingang, 0 akzeptiert und 1 abgelehnt**; alle vier Tasks sind erfolgreich. Das damalige Prüfskript bestätigte beide Snapshots und übersprang bei null akzeptierten Transaktionen den Formatvergleich. Damaliger lokaler Teststand: **243 Tests bestanden**. [Nachweise dieser Nachprüfung](docs/evidence/2026-10-28/revalidation-80af12b/summary.json), [vollständige Athena-Prüfung](docs/evidence/2026-10-28/revalidation-80af12b/full-verification.json). Frühere Messwerte und Bilder behalten ihren damaligen Versionsbezug.

## Auf AWS bereitstellen

Zuletzt am 04.11.2026 in `eu-central-1` bereitgestellt und geprüft. Die folgenden Befehle werden aus dem Projektverzeichnis ausgeführt. Der Helfer `scripts/aws_terraform.py` macht eine gültige AWS-CLI-Anmeldung mit `aws login` für den gepinnten Terraform-Provider 5.x nutzbar und ermöglicht die Erneuerung kurzlebiger Zugangsdaten während langer MWAA-Erstellungen; siehe [Betrieb](docs/operations.md).

Voraussetzungen: die Python-Umgebung mit `requirements.txt` aus dem Schnellstart, erzeugte Eingabedateien unter `data/`, ein AWS-Konto, eine gültige AWS-CLI-Anmeldung und Terraform ab 1.5. Die AWS-Identität benötigt Rechte für die in `terraform/` definierten Ressourcen, einschließlich S3, Glue, IAM, KMS, Lambda, CloudWatch, Athena, SNS, EventBridge und Budgets; bei MWAA zusätzlich dessen Netzwerkressourcen.

```bash
terraform -chdir=terraform init
terraform -chdir=terraform fmt
terraform -chdir=terraform validate
python scripts/aws_terraform.py plan -var="alert_email=du@example.com"
python scripts/aws_terraform.py apply -var="alert_email=du@example.com"   # Abo-Mail von AWS bestätigen
BUCKET=$(terraform -chdir=terraform output -raw bucket_name)
python scripts/upload_dataset.py "$BUCKET" data
```

Das Upload-Skript schreibt die drei Dateien unter eine neue Paket-ID und zuletzt `ready.json`. Erst diese Freigabe startet den Lambda-Trigger. Bereits eingereichte Pakete werden anhand der Glue-Laufhistorie erkannt. Eine dauerhafte bedingte S3-Sperre unter `submissions/` verhindert gleichzeitige automatische Einreichungen desselben Pakets. Weitere Pakete werden in Glue eingereiht. Einzelheiten und Wiederholungen: `docs/operations.md`. Nach erfolgreichem Glue-Lauf mit `python scripts/current_snapshot.py "$BUCKET"` die veröffentlichte Datenbank ermitteln und darin die Abfragen aus `sql/` ausführen. Die Workgroup kommt aus Terraform; die Terraform-Datenbank dient ausschließlich als Schemavorlage.

Bei einer bestehenden Installation benötigen die geänderten Qualitätsalarme und das Dashboard ein Terraform-Apply. Der ETL-Code wird als eigenes Release aktualisiert; Terraform behält den zuvor gewählten Code-Stand bei:

```bash
GLUE_JOB=$(terraform -chdir=terraform output -raw glue_job_name)
python scripts/deploy_release.py "$BUCKET" "$GLUE_JOB"
```

Das Release ergänzt die Kontaktspalten beim Anlegen neuer Snapshot-Kataloge auch bei älteren Schemavorlagen. Das Prüfskript gleicht bei neuen Quittungen zusätzlich Kunden- und Kontenausgaben ab; `verified_datasets` nennt die geprüften Dateien. Einzelheiten: [Betrieb](docs/operations.md).

Aufräumen: zuerst aktive Glue-Läufe stoppen oder auslaufen lassen. Dann aus dem Projektverzeichnis die Snapshot-Kataloge auflisten und anschließend löschen:

```bash
TEMPLATE_DB=$(terraform -chdir=terraform output -raw glue_database)
python scripts/cleanup_snapshot_catalogs.py "$BUCKET" "$TEMPLATE_DB" --include-published
# Nach Prüfung der Liste einschließlich des veröffentlichten Katalogs löschen:
python scripts/cleanup_snapshot_catalogs.py "$BUCKET" "$TEMPLATE_DB" --delete --include-published
python scripts/aws_terraform.py destroy
```

Bucket und Athena-Workgroup werden mit allen Inhalten gelöscht (`force_destroy = true`). Das ist für diese Testdaten gewollt und darf **nicht** für Projekte mit echten Daten übernommen werden.

Optional und standardmäßig **aus**: Amazon MWAA (`enable_mwaa=true`). Die frühere Demo-Infrastruktur wurde am 28.10.2026 entfernt. Am 04.11.2026 wurde `bankguard-airflow` erneut bereitgestellt und mit zwei erfolgreichen DAG-Läufen geprüft; diese Bereitstellung wurde nach den Tests am selben Tag wieder abgebaut. Der manuelle DAG erwartet die drei CSV-Dateien direkt unter `raw/`. MWAA und NAT-Gateway verursachen laufende Kosten. Anleitung: [Betrieb, Abschnitt 6](docs/operations.md#6-optionales-airflow-und-mwaa).

## Projektstruktur

```text
BankGuard-AWS-Data-Engineering/
├── src/
│   ├── __init__.py
│   ├── generate_data.py
│   ├── snapshot.py
│   ├── transform.py
│   └── validate.py
├── glue/
│   └── banking_etl.py
├── lambda_fn/
│   ├── __init__.py
│   ├── check_glue_runs.py
│   └── start_glue_job.py
├── terraform/
│   ├── github_oidc.tf
│   ├── glue.tf
│   ├── iam.tf
│   ├── kms.tf
│   ├── lambda.tf
│   ├── main.tf
│   ├── monitoring.tf
│   ├── mwaa.tf
│   ├── outputs.tf
│   ├── run_monitor.tf
│   ├── s3.tf
│   ├── variables.tf
│   └── .terraform.lock.hcl
├── sql/
│   ├── compare_csv_vs_parquet.sql
│   ├── country_analysis.sql
│   ├── daily_transactions.sql
│   ├── data_quality.sql
│   └── rework_report.sql
├── airflow/
│   ├── dags/
│   │   └── bankguard_pipeline.py
│   ├── requirements.lock
│   └── requirements.txt
├── scripts/
│   ├── athena_scan_compare.py
│   ├── aws_terraform.py
│   ├── cleanup_snapshot_catalogs.py
│   ├── current_snapshot.py
│   ├── deploy_release.py
│   ├── upload_dataset.py
│   └── verify_aws.py
├── examples/
│   ├── README.md
│   ├── accounts.csv
│   ├── customers.csv
│   └── transactions.csv
├── tests/
│   ├── __init__.py
│   ├── test_aws_scripts.py
│   ├── test_customer_contacts.py
│   ├── test_glue_output.py
│   ├── test_lambda_trigger.py
│   ├── test_problem_statement.py
│   ├── test_quality_monitoring.py
│   ├── test_release_and_monitor.py
│   ├── test_sdk_contract.py
│   ├── test_snapshot_publication.py
│   ├── test_sql_reports.py
│   ├── test_terraform_credentials.py
│   ├── test_transform.py
│   └── test_validation.py
├── docs/
│   ├── cost-estimate.md
│   ├── data-model.md
│   ├── ihk-plan.md
│   ├── operations.md
│   ├── overview-en.md
│   ├── structure.md
│   ├── tool-map.md
│   └── evidence/
├── .github/
│   └── workflows/
│       ├── ci.yml
│       └── deploy.yml
├── .dockerignore
├── .gitignore
├── Dockerfile
├── README.md
├── README_ENG.md
├── requirements.lock
├── requirements.txt
└── pytest.ini
```

Die Übersicht zeigt Quellcode und Projektdokumentation. Generierte Daten, Ausgaben, lokale Umgebungen und Terraform-State sind ausgelassen.

## Tests

Automatische Tests; die aktuelle Anzahl zeigt `python -m pytest -q`. `tests/test_problem_statement.py` hat einen Test pro Satz der Problembeschreibung, unter anderem:

- Das Beispiel in `examples/` liefert genau das erwartete Ergebnis.
- **Kein Datensatz geht verloren:** Jede Eingabezeile steht genau einmal in „gültig" oder „abgelehnt".
- **Berichte sehen nur Gültiges:** keine doppelten IDs, keine unbekannten Konten, keine ungültigen Beträge oder Daten.
- Jeder abgelehnte Datensatz hat mindestens einen Grund.
- Jedes Herkunftssystem geht für sich auf.
- Ein zweiter Lauf mit denselben Daten liefert dieselben Ergebnisse (nur `run_id` und Zeitstempel unterscheiden sich).


## Bekannte Grenzen

Ein Konto mit Status `Closed` bleibt nach den aktuellen Regeln gültig. Ein Abgleich des Transaktionsdatums mit einem Schließungszeitpunkt ist nicht implementiert. Kontaktprüfungen bestätigen nur das Format, keine Erreichbarkeit.

Der veröffentlichte Snapshot wird über ein einzelnes S3-Objekt ausgewählt. Dieses einmal auflösen und die ausgewählte Snapshot-Datenbank für alle zusammengehörigen Abfragen verwenden. Alte Snapshots und ihre Katalogdatenbanken bleiben bis zur bewussten Bereinigung bestehen. Die Validierung sammelt die kleinen Demo-Dateien im Arbeitsspeicher; große Datenmengen benötigen verteilte Regeln. Paket-IDs dürfen nicht wiederverwendet werden. Dauerhafte S3-Sperren verhindern parallele automatische Starts desselben Pakets. Unterbrochene oder unklare Einreichungen sowie fehlgeschlagene oder abgelaufene Läufe erfordern eine Prüfung und gegebenenfalls einen bewussten Neustart. Bereits vor Einführung der Sperren eingereichte Pakete sind nur durch die vorhandene Glue-Historie geschützt. Der zentrale AWS-Ablauf und zwei manuelle MWAA-Läufe sind am 04.11.2026 verifiziert; Langzeitbetrieb und Lastverhalten wurden nicht gemessen. Die frühere SNS-Metrik vom 28.10.2026 bestätigt eine Zustellung im damaligen Deployment. Die Alarmzustellung im neuen Deployment und die Sichtbarkeit im persönlichen E-Mail-Postfach sind separat zu prüfen.

Code-Releases liegen unter einer Inhalts-Hash-ID; Skript und Bibliothek werden erst nach vollständigem Upload gemeinsam umgeschaltet. Ein zusätzlicher Prüflauf kontrolliert alle fünf Minuten die Glue-Zustände ERROR und EXPIRED.

Der Formatvergleich verwendet dieselben gültigen Datensätze und prüft Anzahl und Summen. Er misst CSV gegenüber partitioniertem Parquet und damit Format und Partitionierung gemeinsam. Anleitung: `docs/cost-estimate.md`.

## Hilfsmittel

Python, Pytest, boto3, Terraform, AWS Glue, S3 und Athena bilden den Kern. GitHub Actions, Docker und Airflow unterstützen Tests, Bereitstellung und Demonstration. Verifizierungsstand: `docs/tool-map.md`.

## Weitere Dokumente

- `docs/data-model.md`: Datenmodell, Bereiche in S3, Tabellen, Fehlercodes
- `docs/operations.md`: Automatisierung, Zugriffsrechte, Überwachung, MWAA
- `docs/ihk-plan.md`: Zeitplan und Präsentation
- `docs/tool-map.md`: Werkzeugliste mit ehrlichem Stand
- [docs/cost-estimate.md](docs/cost-estimate.md): historische Messwerte, Kostenberechnung und Vorlage für weitere Nutzung
- `docs/overview-en.md`: technische Übersicht auf Englisch

*BankGuard ist eine Lern- und Demonstrationsarbeit mit synthetischen Daten, kein Produktivsystem.*
