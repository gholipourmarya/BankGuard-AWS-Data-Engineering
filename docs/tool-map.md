# Werkzeuge und Verifizierungsstand

| Werkzeug | Aufgabe | Stand |
| --- | --- | --- |
| Python und Standardbibliothek | Generator, CSV-Parser und Validierung | Lokal ausgeführt |
| Pytest | Regeln, Wiederholungen, Parser und simulierte AWS-Schnittstellen | Lokal ausgeführt; Anzahl durch pytest ermitteln |
| boto3 | Upload und Athena-Skript; AWS-Aufrufe im Glue-Job und Trigger | Upload, Veröffentlichung und Athena-Abfragen auf AWS ausgeführt |
| Terraform | Infrastruktur und Rollen | Validiert und angewendet; OIDC-Deployment-Rolle ergänzt |
| AWS Glue und Spark | Getrennte Snapshot-Ausgaben und Kataloge, Veröffentlichung und Vergleichs-CSV | Cloud-Lauf erfolgreich: 135 Sekunden, 51.000 Transaktionen |
| S3, KMS, Lambda und SNS | Datenpakete, Verschlüsselung, Start, periodische Laufprüfung und Fehlerbenachrichtigung | Automatischer Start, KMS, Versionierung, Public-Access-Block und SNS-Zustellmetrik geprüft |
| Athena | SQL-Berichte und Scanvergleich | SQL-Berichte und gleicher CSV-/Parquet-Datenbestand geprüft; Scanmessung gesichert |
| GitHub Actions | CI und manueller Glue-Code-Upload über OIDC | CI und OIDC-Deployment auf feature/bankguard erfolgreich; Links und Commit-Stand siehe Betrieb |
| Docker | Lokale Tests und Verarbeitung im Container | Image gebaut und ausgeführt: 113 Tests und vollständige Verarbeitung erfolgreich |
| Airflow und optional MWAA | Manueller Lauf und Prüfung seiner Erfolgsquittung | Funktionen lokal simuliert; Airflow- und MWAA-Lauf offen |

Für Befehle und Voraussetzungen siehe [Betrieb](operations.md). Ein erfolgreicher Test mit simuliertem AWS-Client beweist keine IAM-, Netzwerk- oder Cloud-Laufzeitfunktion.

Nachweise vom 28.10.2026: [AWS-Abfragen](evidence/2026-10-28/aws-verification.json), [CloudWatch](evidence/2026-10-28/cloudwatch-records.png), [Kosten](cost-estimate.md). SNS meldet Zustellung; das persönliche E-Mail-Postfach wurde nicht eingesehen. Ein echter ERROR-/EXPIRED-Zustand des periodischen Prüfers wurde nicht erzeugt.
