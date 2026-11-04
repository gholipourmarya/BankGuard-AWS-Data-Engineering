# Terraform-Abbau am 28.10.2026

Auf ausdrücklichen Wunsch wurde die Demo nach den erfolgreichen AWS-Integrationstests abgebaut. Alle 70 zuvor verwalteten Terraform-Ressourcen sind aus dem State entfernt: 42 im ersten Lauf, MWAA anschließend durch AWS und 27 im abschließenden Lauf. Eine abgelaufene AWS-Sitzung unterbrach den ersten Lauf; nach erneuter Anmeldung und bestätigter MWAA-Löschung wurde ein frischer Restplan ausgeführt.

Die abschließenden AWS-Abfragen bestätigen: MWAA und VPC nicht vorhanden, NAT gelöscht, beide Buckets nicht vorhanden, Glue-Job nicht vorhanden und keine BankGuard-Katalogdatenbanken mehr. Acht zur Laufzeit erzeugte Snapshot-Datenbanken wurden vorab entfernt. Der KMS-Schlüssel hat den Status `PendingDeletion`, Löschtermin **04.11.2026, 22:27:59 MEZ**.

CloudWatch-Logs außerhalb von Terraform und der gemeinsam genutzte GitHub-OIDC-Provider bleiben erhalten. Bereits angefallene Kosten und verbleibende Log-Speicherung sind durch diesen Abbau nicht aufgehoben. Lokaler Quellcode und historische Testnachweise bleiben erhalten; die entfernten AWS-Snapshots können nicht mehr abgefragt werden. Ein späteres Apply erstellt die Infrastruktur erneut.

Siehe [Zusammenfassung](summary.json), [AWS-Prüfung](final-aws-checks.json) und [abschließendes Terraform-Protokoll](remaining-destroy.txt). Terraform-State und Zugangsdaten sind nicht Bestandteil dieser Nachweise.
