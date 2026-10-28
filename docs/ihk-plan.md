# IHK Projektplan und Nachweise

BankGuard untersucht, wie fehlerhafte synthetische Banktransaktionen vor einer Auswertung automatisch erkannt und isoliert werden. Die Präsentation soll Problem, Entscheidung, Umsetzung und überprüfbare Ergebnisse verbinden.

## Arbeitspakete

| Paket | Ergebnis | Stand |
| --- | --- | --- |
| Anforderungen | Pflichtfelder, Dublettenregel, Fremdschlüssel, Betrags- und Datumsregeln | Im Code und Datenmodell beschrieben |
| Lokaler Prototyp | Generator, Validierung, Bilanz und Nacharbeit | Lokal getestet |
| Infrastruktur | S3, Glue, KMS, Lambda, Athena und Monitoring | Auf AWS bereitgestellt; Konfiguration geprüft |
| Integration | Paket-Upload, Glue-Lauf, SQL und Alarmtest | Erfolgs- und Fehlerlauf ausgeführt; EventBridge/SNS-Zustellung gemessen |
| Auswertung | Fehlererkennung, Laufzeit und Formatvergleich | Lokale und AWS-Messwerte sowie Formatvergleich gesichert |
| Dokumentation | Architektur, Betrieb, Kosten und Grenzen | Im Repository vorhanden |
| Präsentation | Folien, Live-Demo oder Ersatznachweise | Sprechfolge und Demo-Befehle unten vorbereitet; persönlicher Probelauf ausstehend |

Verbindliche Stunden, Abgabefristen und Prüfungsvorgaben aus dem genehmigten Projektantrag übernehmen; hier werden keine formalen IHK-Vorgaben angenommen.

## Benötigte Nachweise

- Erfolgreicher lokaler Testlauf mit Datum und Commit-ID.
- Terraform-Plan sowie nach Bereitstellung die relevanten AWS-Ressourcen.
- Eine Paket-ID und zugehörige Glue-Run-ID mit Logs und Erfolgsquittung.
- Nachweis Eingang = akzeptiert + abgelehnt sowie Fehlerbeispiele mit Gründen.
- Athena-Bericht und fairer CSV-/Parquet-Vergleich für denselben Datenbestand.
- Bewusst ausgelöster Fehler mit bestätigter Alarmzustellung.
- Kostenübersicht mit Annahmen und dokumentierter Bereinigung.

## Präsentationsfolge

Ausgangsproblem und Projektziel erklären, danach die Architektur und die Reihenfolge Kunden → Konten → Transaktionen zeigen. An einem kleinen Beispiel die Trennung und Nacharbeit demonstrieren. Anschließend gemessene Ergebnisse, Grenzen und wirtschaftliche Bewertung vorstellen. Lokale Resultate und AWS-Resultate ausdrücklich unterscheiden.


## Präsentationsentwurf mit vorhandenen Nachweisen

| Folie | Inhalt und Nachweis |
| --- | --- |
| 1. Ausgangsproblem | Fehlerhafte Banktransaktionen verfälschen Auswertungen und erzeugen Nacharbeit. Projektziel: Fehler erkennen, begründen und isolieren. |
| 2. Anforderungen | Pflichtfelder, eindeutige IDs, gültige Beträge und Daten, Referenzen auf gültige Konten; keine still verlorenen Datensätze. |
| 3. Architektur | S3-Paket → Lambda → Glue → getrennte Snapshot-Ausgaben → Glue Data Catalog → Athena. Kunden vor Konten vor Transaktionen prüfen. |
| 4. Umsetzung | Gemeinsamer Python-Prüfcode lokal und in Glue; Terraform, KMS, unveränderliche Releases und atomarer Veröffentlichungsverweis. |
| 5. Nachweis der Funktion | Paket `172f45007c524d918708385d5ef7d086`; erfolgreicher Lauf mit 51.000 Eingang = 47.500 gültig + 3.500 abgelehnt. [Logs](evidence/2026-10-28/success-logs.json). |
| 6. Nacharbeit und SQL | Herkunftssystem und sämtliche Fehlergründe zeigen; Athena-Ausgaben in [aws-verification.json](evidence/2026-10-28/aws-verification.json). |
| 7. Betrieb und Fehler | Fehlende Datei erzeugt FAILED, bisheriger Snapshot bleibt unverändert, EventBridge/SNS melden Zustellung. [Fehlerlauf](evidence/2026-10-28/failure-run.json). |
| 8. Wirtschaftlichkeit | 135 Sekunden Glue-Ausführung; 0,033 USD berechneter Glue-Listenpreis. Scanmenge CSV/Parquet 2.809.296/1.355 Byte; bei dieser Demo gleiche Mindestabrechnung. Weitere Infrastrukturkosten gesondert erklären. |
| 9. Qualität und Grenzen | 113 lokale/Container-Tests; synthetische Daten, begrenzter Arbeitsspeicher und zeitlich begrenzte Deduplizierung. Optionales MWAA wurde nicht getestet. |
| 10. Ergebnis und Ausblick | Funktionsfähige automatische Datenqualitätsprüfung; größere Datenmengen, belastbare Deduplizierung und längerfristige Betriebsmessung als Ausbau. |

## Kurze Demo ohne neue Cloud-Ressourcen

```bash
source .venv/bin/activate
python -m src.transform examples output_example
cat output_example/rework_report.csv
python -m pytest -q
BUCKET=$(terraform -chdir=terraform output -raw bucket_name)
python scripts/current_snapshot.py "$BUCKET"
# Führt kostenpflichtige SQL-Abfragen aus, startet keinen neuen Glue-Lauf:
python scripts/verify_aws.py "$BUCKET"
```

Als Ersatz bei Netzwerkproblemen die gespeicherten JSON-Ergebnisse und das [exportierte CloudWatch-Diagramm](evidence/2026-10-28/cloudwatch-records.png) verwenden. Noch persönlich zu erledigen: Zeitvorgaben aus dem genehmigten Antrag übernehmen, den Vortrag proben und den Eingang der Alarmmail im Postfach bestätigen. Reale Arbeitszeiten und IHK-Freigaben werden nicht nachträglich erfunden.
