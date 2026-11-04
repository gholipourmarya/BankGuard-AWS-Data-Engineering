# Kleines Verarbeitungsbeispiel

Die drei CSV-Dateien enthalten synthetische Daten: drei gültige Kunden, drei gültige Konten und elf Transaktionszeilen. Sechs Transaktionen sind absichtlich fehlerhaft, damit Ablehnung und Nacharbeitsbericht sichtbar werden.

Aus dem Projektverzeichnis mit installierten Abhängigkeiten ausführen:

```bash
python -m src.transform examples output_example
cat output_example/metrics.json
cat output_example/rework_report.csv
cat output_example/dataset_rework_report.csv
```

Erwartetes Ergebnis:

| Datensatz | Eingang | Akzeptiert | Abgelehnt |
| --- | --- | --- | --- |
| Kunden | 3 | 3 | 0 |
| Konten | 3 | 3 | 0 |
| Transaktionen | 11 | 5 | 6 |
| Gesamt (`overall`) | 17 | 11 | 6 |

Die obersten Zähler in `metrics.json` beziehen sich auf Transaktionen: Erfolgsquote 45,45 %. Die Gesamtquote beträgt 64,71 %. Lauf-ID und Zeitstempel ändern sich pro Ausführung.

| CSV-Zeile in transactions.csv (Kopfzeile = 1) | ID | Eingebauter Fehler | Erwarteter Code |
| --- | --- | --- | --- |
| 4 | T003 | Unbekanntes Konto A999 | `ACCOUNT_NOT_FOUND` |
| 5 | T004 | Fehlender Betrag (`NULL`) | `MISSING_AMOUNT` |
| 6 | T001 | Wiederholung der ID aus Zeile 2 | `DUPLICATE_TRANSACTION_ID` |
| 7 | T005 | Nicht numerischer Betrag `abc` | `INVALID_AMOUNT` |
| 8 | T006 | Ungültiges Datum | `INVALID_DATE` |
| 10 | T008 | Negativer Betrag bei PAYMENT | `AMOUNT_NOT_ALLOWED_FOR_TYPE` |

T007 ist gültig: Ein negativer Betrag bei `REFUND` ist erlaubt. Ein Konto mit Status `Closed` ist nach den aktuellen Regeln ebenfalls gültig; eine zusätzliche Prüfung des Transaktionsdatums gegen einen Schließungszeitpunkt ist nicht implementiert.

Die Kundenkontakte erfüllen die Formatregeln. Die Beispieladressen unter `example.com` und die Telefonnummern dienen ausschließlich als synthetische Eingabewerte; ihre Erreichbarkeit wird nicht geprüft. Dieses Beispiel deckt sechs Transaktionsfehler ab. Weitere Fehlerfälle einschließlich ungültiger Kontakte und Referenzdaten stehen in den [Tests](../tests/) und im [Datenmodell](../docs/data-model.md).

Die lokalen Ausgaben bestehen aus `curated_*.csv`, `rejected_*.jsonl`, den beiden Nacharbeitsberichten und `metrics.json`. Parquet wird vom AWS-Glue-Job geschrieben.
