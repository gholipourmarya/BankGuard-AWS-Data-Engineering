# Aufbau des Repositorys

| Pfad | Aufgabe |
| --- | --- |
| src/validate.py | Gemeinsame Prüfregeln ohne AWS-Abhängigkeit |
| src/transform.py | Gemeinsamer CSV-Parser, Pipeline und lokale Ausgaben |
| src/snapshot.py | Getrennte Snapshot-Kataloge, Partitionsregistrierung und Veröffentlichung |
| src/generate_data.py | Reproduzierbare synthetische Daten und Fehlerprotokoll |
| glue/banking_etl.py | Glue-Ausführung, Parquet, Vergleichs-CSV, Partitionsregistrierung und Laufquittung |
| lambda_fn/start_glue_job.py | Freigegebene Pakete erkennen und dedupliziert einreichen |
| lambda_fn/check_glue_runs.py | Geplante Kontrolle von ERROR und EXPIRED mit Versandquittungen |
| scripts/deploy_release.py | Zusammengehörige unveränderliche Code-Releases gemeinsam aktivieren |
| scripts/current_snapshot.py | Veröffentlichten Snapshot einmal auflösen |
| scripts/cleanup_snapshot_catalogs.py | Snapshot-Kataloge anzeigen und ausdrücklich bereinigen |
| scripts/upload_dataset.py | Drei Dateien unter eindeutiger ID hochladen und zuletzt freigeben |
| scripts/athena_scan_compare.py | Gleiche SQL-Ergebnisse und gescannte Bytes vergleichen |
| scripts/aws_terraform.py | AWS-CLI-Anmeldung über temporäres credential_process-Profil mit erneuerbaren Zugangsdaten für Terraform verwenden |
| scripts/verify_aws.py | Veröffentlichten AWS-Snapshot, SQL-Berichte und Formatvergleich prüfen und sichern |
| docs/evidence/2026-10-28/ | Historische AWS-Ergebnisse, Logs, Preisgrundlagen, Diagramm und Abbau-Nachweise |
| docs/evidence/2026-11-04/ | AWS-Abgleich aller drei Datensätze und zwei erfolgreiche MWAA-Läufe der neuen Bereitstellung |
| terraform/ | Infrastruktur, Rollen, Überwachung und optional MWAA |
| sql/ | Abfragen für die im Athena-Editor ausgewählte Snapshot-Datenbank |
| airflow/ | Optionaler manueller DAG und eigene Abhängigkeiten |
| examples/ | Kleiner fester Beispieldatensatz mit sechs absichtlichen Transaktionsfehlern; Erklärung in [examples/README.md](../examples/README.md) |
| tests/ | Lokale Regeln, Regressionen und AWS-Simulationen |
| .github/workflows/ | CI und manueller Glue-Code-Deploy |
| docs/ | Datenmodell, Betrieb, Kosten, Projektplan und Werkzeugstand |

`data/`, `output/`, `output_example/`, `.venv/`, `build/`, Terraform-State und Provider-Verzeichnisse sind erzeugte oder lokale Dateien. Sie gehören nicht ins Repository. Die Terraform-Provider-Lockdatei sowie `requirements.lock` und `airflow/requirements.lock` werden eingecheckt. Die optionalen Airflow-Pakete werden in einer getrennten Umgebung installiert. Zugangsdaten werden außerhalb des Codes verwaltet.
