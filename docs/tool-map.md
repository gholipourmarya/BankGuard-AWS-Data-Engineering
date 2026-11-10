# Werkzeuge und Verifizierungsstand

**Betriebsstatus nach dem Abbau am 04.11.2026:** Nach den erfolgreichen Tests wurden alle 73 Terraform-Ressourcen und drei zusätzliche Snapshot-Kataloge entfernt; der State ist leer. Der KMS-Schlüssel ist zur Löschung vorgemerkt. CloudWatch-Logs außerhalb des State und der gemeinsam genutzte GitHub-OIDC-Provider bleiben erhalten. [Abbau-Nachweis](evidence/2026-11-04/terraform-destroy/summary.json). Die Prüfungen vom 04.11.2026 beschreiben den vorherigen Teststand. Betriebsbefehle aus dem Projektverzeichnis ausführen.

| Werkzeug | Aufgabe | Stand |
| --- | --- | --- |
| Python und Standardbibliothek | Generator, CSV-Parser und Validierung | Lokal ausgeführt |
| Pytest | Regeln, Wiederholungen, Parser und simulierte AWS-Schnittstellen | Lokal ausgeführt; Anzahl durch pytest ermitteln |
| boto3 | Upload und Athena-Skript; AWS-Aufrufe im Glue-Job und Trigger | Upload, Veröffentlichung und Athena-Abfragen auf AWS ausgeführt |
| Terraform | Infrastruktur und Rollen | Validiert und angewendet; OIDC-Deployment-Rolle ergänzt |
| AWS Glue und Spark | Getrennte Snapshot-Ausgaben und Kataloge, Veröffentlichung und Vergleichs-CSV | Am 04.11.2026 verifiziert: 51.000 Transaktionen, davon 47.500 akzeptiert und 3.500 abgelehnt |
| S3, KMS, Lambda und SNS | Datenpakete, Verschlüsselung, Start, periodische Laufprüfung und Fehlerbenachrichtigung | Am 04.11.2026 bereitgestellt; automatischer Paketlauf erfolgreich. KMS, Versionierung, Public-Access-Block und SNS-Zustellmetrik im historischen Test vom 28.10.2026 geprüft |
| Athena | SQL-Berichte und Scanvergleich | SQL-Berichte und gleicher CSV-/Parquet-Datenbestand geprüft; Scanmessung gesichert |
| GitHub Actions | CI und manueller Glue-Code-Upload über OIDC | CI und OIDC-Deployment auf feature/bankguard erfolgreich; Links und Commit-Stand siehe Betrieb |
| Docker | Lokale Tests und Verarbeitung im Container | Aktuelles Image gebaut und ausgeführt: 378 Tests und vollständige Verarbeitung erfolgreich |
| Airflow und optional MWAA | Manueller Lauf und Prüfung seiner Erfolgsquittung | Neue Bereitstellung nach Tests wieder abgebaut; zuvor zwei erfolgreiche DAG-Läufe am 04.11.2026, Airflow 3.3.1 / mw1.micro; [API-Nachweis](evidence/2026-11-04/airflow-runs.json) |

Für Befehle und Voraussetzungen siehe [Betrieb](operations.md). Ein erfolgreicher Test mit simuliertem AWS-Client beweist keine IAM-, Netzwerk- oder Cloud-Laufzeitfunktion.

Nachweise vom 28.10.2026: [AWS-Abfragen](evidence/2026-10-28/aws-verification.json), [CloudWatch](evidence/2026-10-28/cloudwatch-records.png), [Kosten](cost-estimate.md). SNS meldet Zustellung; das persönliche E-Mail-Postfach wurde nicht eingesehen. Ein echter ERROR-/EXPIRED-Zustand des periodischen Prüfers wurde nicht erzeugt.

Historische Nachprüfung vom 28.10.2026: ETL-Release aus Commit `80af12b` auf AWS bereitgestellt; kompletter Paket-Lauf und MWAA-Lauf mit null akzeptierten Transaktionen erfolgreich. Damals bestanden 243 lokale Tests. [Historische Zusammenfassung](evidence/2026-10-28/revalidation-80af12b/summary.json). Am 04.11.2026 bestätigt das aktuelle Prüfskript alle drei Datensätze mit zwölf Athena-Abfragen und dem CSV-/Parquet-Vergleich; lokal bestehen 378 Tests.
