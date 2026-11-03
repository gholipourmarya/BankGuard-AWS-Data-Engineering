# Kosten und Formatvergleich

Am 28.10.2026 wurden Glue und Athena in `eu-central-1` ausgeführt. Die folgenden Werte trennen gemessene Nutzung, daraus berechnete Listenpreise und verzögert eintreffende Abrechnungsdaten. Preise wurden über die AWS Price List API abgerufen; die verwendeten Produkte stehen in [prices.json](evidence/2026-10-28/prices.json).

## Kostenblatt

| Bestandteil | Zu erfassende Menge | Berechnung |
| --- | --- | --- |
| Glue | DPU-Stunden pro Lauf und Läufe pro Monat | DPU-Stunden × regionaler Tarif |
| S3 | Speicher je Klasse, alte Versionen, Snapshots, Katalogobjekte und Requests | Mengen × jeweilige Tarife |
| KMS | Schlüssel und API-Aufrufe | Schlüsselzeit + Aufrufe |
| Athena | abgerechnete Scanmenge | Scanmenge × regionaler Tarif unter Beachtung der Abrechnungsregeln |
| Lambda, SNS, CloudWatch | Aufrufe, Laufzeit, Logs und Alarme | jeweilige Nutzung × Tarif |
| Optional MWAA und NAT | Betriebsstunden und Datentransfer | Stunden × Tarife + Transfer |

Annahmen und Messdatum neben jede ausgefüllte Zahl schreiben. Free-Tier-Leistungen nur berücksichtigen, wenn sie für das konkrete Konto gelten. Das Terraform-Budget dient als Warnung und ist keine Ausgabensperre. Nach der Demo Ressourcen entfernen.

## Reproduzierbarer CSV und Parquet Vergleich

1. Ein Datenpaket mit Glue erfolgreich verarbeiten lassen. Glue schreibt dieselben akzeptierten Transaktionen unter `snapshots/<run-id>/comparison/csv/` und `snapshots/<run-id>/curated/transactions/`.
2. Den Verweis auf den veröffentlichten Snapshot einmal auflösen. Beide Abfragen müssen dieselbe unveränderte Snapshot-Datenbank verwenden. Neue Glue-Läufe dürfen parallel eigene Snapshots erstellen. Das Vergleichs-CSV darf nicht durch Rohdaten ersetzt werden.
3. Ein Datum mit akzeptierten Transaktionen auswählen und die richtige Datenbank und Workgroup verwenden:

```bash
python scripts/athena_scan_compare.py 2026-03-04 DEINE_WORKGROUP --bucket DEIN_BUCKET
# Alternativ einen bestimmten Snapshot auswählen:
python scripts/athena_scan_compare.py 2026-03-04 DEINE_WORKGROUP --database DEINE_SNAPSHOT_DATENBANK
```

Das Skript deaktiviert die Wiederverwendung von Abfrageergebnissen, begrenzt die Wartezeit und vergleicht Währungen, Anzahlen und Summen. Bei Abweichungen oder leeren Ergebnissen wird keine Vergleichsmessung ausgegeben. `TRY_CAST` verhindert einen unmittelbaren Cast-Abbruch bei versehentlich verunreinigtem Vergleichs-CSV; unterschiedliche Ergebnisse brechen den Vergleich trotzdem ab.

| Messwert | CSV | Parquet |
| --- | --- | --- |
| Query-ID (28.10.2026) | `cce1ab25-1a77-45b3-9ae3-f0f458162a04` | `51871b1d-6b8d-4ebd-affc-f2131e20a2f4` |
| Tag | 2026-01-01 | 2026-01-01 |
| EUR: Anzahl / Summe | 123 / 221.367,01 | 123 / 221.367,01 |
| USD: Anzahl / Summe | 43 / 76.416,60 | 43 / 76.416,60 |
| DataScannedInBytes | 2.809.296 | 1.355 |
| EngineExecutionTimeInMillis | 442 | 456 |

Verhältnis = gescannte CSV-Bytes / gescannte Parquet-Bytes. Das Experiment vergleicht CSV ohne Datumspartitionierung mit nach Datum partitioniertem Parquet; es misst damit Format, Spaltenauswahl und Partitionierung gemeinsam. AWS-Mindestabrechnungen und Cache-Effekte getrennt erläutern. Eine einzelne Messung ist kein allgemeiner Leistungsnachweis.


## Berechnete Kosten der Demo

Die [Berechnung](evidence/2026-10-28/cost-calculation.json) basiert auf dem [echten Glue-Lauf und den zehn Athena-Abfragen](evidence/2026-10-28/aws-verification.json). USD, vor Steuern und ohne Gutschriften oder Rabatte:

| Bestandteil | Nutzung und Tarif | Berechneter Listenpreis |
| --- | --- | --- |
| Erfolgreicher Glue-Lauf | 270 DPU-Sekunden / 3.600 × 0,44 USD je DPU-Stunde | 0,03300 USD |
| Bewusster Glue-Fehlerlauf | 52 Sekunden mit 2 Workern; auf 60 Sekunden Mindestdauer angesetzt | 0,01467 USD |
| Zehn Athena-Abfragen | Jede unter 10 MiB; jeweils Mindestabrechnung, 5 USD je TiB | 0,000477 USD zusammen |
| Annahme: 30 gleich lange erfolgreiche Glue-Läufe im Monat | 30 × 0,033 USD | 0,99 USD nur Glue |
| Ein symmetrischer KMS-Schlüssel ohne zusätzliche Rotation | ein voller Monat | 1,00 USD plus API-Aufrufe |
| S3 Standard | erste Preisstufe in Frankfurt | 0,0245 USD je GB-Monat plus Requests |

Die Glue-Laufzeit ist die von AWS gemeldete `ExecutionTime`, nicht die lokale Prüfzeit oder die gesamte Zeit von Upload bis Veröffentlichung. Die Fehlerlaufkosten berücksichtigen die Mindestdauer. [Glue-Preisregeln](https://aws.amazon.com/glue/pricing/), [Athena-Preisregeln](https://aws.amazon.com/athena/pricing/), [KMS-Preise](https://aws.amazon.com/kms/pricing/) und [S3-Preise](https://aws.amazon.com/s3/pricing/).

Das Scanverhältnis ist **2.073,28 zu 1**. Trotzdem fallen beide Vergleichsabfragen unter Athenas Mindestabrechnung; in diesem Versuch ist daraus keine niedrigere Abfragegebühr ableitbar. Die Parquet-Abfrage war mit 456 ms gegenüber 442 ms auch nicht schneller. Eine allgemeine Beschleunigung wird daraus nicht behauptet.

Dies ist keine vollständige Monatsrechnung: S3-Requests und Versionen, KMS-Aufrufe, Glue-Katalog, Lambda, SNS, CloudWatch-Dashboard, Alarme und Logs kommen je nach Nutzung hinzu. Zum Zeitpunkt dieser ersten Messung waren Airflow/MWAA und NAT noch nicht angelegt. Inzwischen wurde MWAA für die Demo aktiviert und ein DAG erfolgreich ausgeführt; MWAA- und NAT-Betriebsstunden sowie die zusätzliche Glue-/Athena-Nutzung sind in den oben berechneten Kosten nicht enthalten und müssen für die Gesamtrechnung ergänzt werden. Free-Tier-Leistungen wurden nicht abgezogen. Der [Budget-API-Stand](evidence/2026-10-28/budget.json) meldete zum Abruf 0,00 USD Ist und 0,04 USD Prognose bei 10 USD Limit; das sind verzögerte, kontoweite Abrechnungsdaten und kein Beweis für kostenlose Testläufe.

Die Infrastruktur bleibt für die Demo bestehen. Nach der Präsentation gemäß [Betrieb, Abschnitt 7](operations.md#7-aufräumen) bereinigen; die destruktive Bereinigung wurde nicht ausgeführt.
