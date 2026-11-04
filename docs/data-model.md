# Datenmodell und Prüfregeln

BankGuard verarbeitet synthetische Kunden, Konten und Transaktionen. Ein Konto verweist auf einen gültigen Kunden, eine Transaktion auf ein gültiges Konto.

| Datei | Schlüssel | Weitere Pflichtfelder |
| --- | --- | --- |
| customers.csv | customer_id | name, country |
| accounts.csv | account_id | customer_id, status |
| transactions.csv | transaction_id | account_id, amount, currency, transaction_type, transaction_date, source_system |

`customers.csv` unterstützt zusätzlich die optionalen Spalten `email` und `tel` (Telefonnummer als Zeichenkette). Beide dürfen fehlen oder leer bleiben; akzeptierte Ausgaben enthalten sie dann als leere Zeichenketten. Der Generator schreibt beide Spalten mit synthetischen Kontaktdaten.

Ausgefüllte E-Mail-Adressen müssen eine einfache ASCII-Mailboxsyntax mit Domain und mindestens einem Punkt erfüllen (maximal 254 Zeichen, höchstens 64 vor `@`, keine leeren Domainteile oder aufeinanderfolgenden Punkte im lokalen Teil). Die Prüfung bestätigt keine Erreichbarkeit. Telefonnummern erlauben 7 bis 15 ASCII-Ziffern, ein optionales führendes `+` und die Trennzeichen Leerzeichen, Klammern, `/`, `.`, `-`. Klammern müssen geschlossen sein und nicht leere Zifferngruppen ohne weitere Klammern bilden. Nach einem optionalen `+` beginnt die Nummer mit einer Ziffer oder öffnenden Klammer und endet mit einer Ziffer oder schließenden Klammer. Ungültige Kontaktwerte erhalten `INVALID_EMAIL` bzw. `INVALID_TEL`; abgelehnte Zeilen behalten die Originalwerte.

Die CSV-Kopfzeile muss alle Pflichtspalten ohne doppelte Namen enthalten; ihre Reihenfolge darf abweichen. Bei Kunden sind zusätzlich `email` und `tel` erlaubt, andere unbekannte Spalten bleiben unzulässig. CSV-Syntaxfehler und ungültige Kopfzeilen stoppen den Lauf ausdrücklich. Zeilen mit zu vielen oder zu wenigen Zellen erhalten `INVALID_COLUMN_COUNT`; zusätzliche Werte bleiben unter `extra_values` erhalten. Zeilennummern beziehen sich auf die erste physische Zeile des Datensatzes. Der gemeinsame Parser unterstützt UTF-8-BOM, zitierte Felder und eingebettete Zeilenumbrüche.

## Gültige Werte

Beträge müssen ohne Wertänderung als `DECIMAL(18,2)` darstellbar sein: höchstens 16 Stellen vor und zwei werttragende Stellen nach dem Komma. Zusätzliche abschließende Nullen sind erlaubt. Es wird nicht gerundet. Null ist unzulässig; negative Werte sind nur für `REFUND` und `REVERSAL` erlaubt. Weitere Transaktionsarten: `DEPOSIT`, `WITHDRAWAL`, `PAYMENT`, `TRANSFER`.

Datumswerte müssen existieren und nach Entfernen äußerer Leerzeichen genau `JJJJ-MM-TT` entsprechen. Es gibt keine Beschränkung auf das Jahr der Testdaten. Glue registriert tatsächlich ausgegebene Tage im Data Catalog.

Zulässige Währungen: `EUR`, `USD`. Kontostatus: `Active`, `Closed`. Herkunftssysteme: `CORE_BANKING`, `CARDS`, `ONLINE_BANKING`. Äußere Leerzeichen werden bei akzeptierten Zeichenketten entfernt, damit Fremdschlüssel in SQL übereinstimmen. Abgelehnte Zeilen behalten ihre ursprünglichen Werte.

IDs dürfen nach Entfernen äußerer Leerzeichen keine ASCII- oder Unicode-Steuerzeichen der Kategorie `Cc` enthalten (einschließlich `U+0085`). Eingebettete Zeilenumbrüche werden als `INVALID_CUSTOMER_ID`, `INVALID_ACCOUNT_ID` oder `INVALID_TRANSACTION_ID` abgelehnt. Mehrzeilige Namen bleiben zulässig; sie werden nicht in den Transaktions-CSV-Vergleich exportiert.

Bei doppelten IDs ist die niedrigste Quellzeilennummer das Original. Alle späteren Vorkommen werden abgelehnt, auch wenn das Original selbst ungültig war. Abweichende Transaktionsinhalte erhalten zusätzlich `DUPLICATE_CONFLICTING_CONTENT`.

## Fehler und Auditdaten

Fehlercodes: `MISSING_<FELD>`, `INVALID_COLUMN_COUNT`, `INVALID_CUSTOMER_ID`, `INVALID_ACCOUNT_ID`, `INVALID_TRANSACTION_ID`, `INVALID_AMOUNT`, `INVALID_DATE`, `INVALID_CURRENCY`, `INVALID_TRANSACTION_TYPE`, `INVALID_SOURCE_SYSTEM`, `INVALID_STATUS`, `INVALID_EMAIL`, `INVALID_TEL`, `AMOUNT_NOT_ALLOWED_FOR_TYPE`, `CUSTOMER_NOT_FOUND`, `ACCOUNT_NOT_FOUND` sowie `DUPLICATE_CUSTOMER_ID`, `DUPLICATE_ACCOUNT_ID`, `DUPLICATE_TRANSACTION_ID` und `DUPLICATE_CONFLICTING_CONTENT`.

Eine Zeile kann mehrere Gründe in `error_reasons` haben. Jeder Datensatz landet genau einmal in akzeptiert oder abgelehnt. Ein Nacharbeitsbericht gruppiert Transaktionsfehler nach Herkunftssystem und Grund.

Die obersten Kennzahlen in `metrics.json` sowie `by_source_system` beziehen sich weiterhin auf Transaktionen. `by_dataset` enthält zusätzlich getrennte Kennzahlen für Kunden, Konten und Transaktionen; `overall` summiert alle drei Dateien. CloudWatch erhält für jede Datei und den Gesamtstand eigene Kennzahlen mit den Dimensionen `JobName` und `Dataset`. Qualitätsalarme bewerten jede Datei getrennt; zusätzlich bleibt ein Gesamtalarm bestehen. Leere Dateien senden Zähler mit Wert null, aber keine Erfolgsquote. Dadurch ergibt eine leere Datei keinen falschen Qualitätsfehler. Alte Quittungen ohne diese Ergänzungen bleiben lesbar. Vorhandene Erfolgsquoten müssen numerisch, endlich, zwischen 0 und 100 und identisch mit der auf zwei Nachkommastellen berechneten Quote sein. Lokal enthält `dataset_rework_report.csv` die Fehler aller drei Dateien nach Datensatzart, Fehlergrund und Beispiel-IDs; der bisherige `rework_report.csv` bleibt der Transaktionsbericht nach Herkunftssystem. In Glue liegt der zusätzliche Gesamtbericht als JSON im Snapshot.

Ausgaben tragen `run_id`, `source_file` und `ingestion_timestamp`. In Glue ist `run_id` die AWS-Glue-Lauf-ID; `source_file` enthält den vollständigen Paketpfad, der Zeitstempel ist UTC. Abgelehnte Zeilen enthalten zusätzlich `line`.

## Speicherbereiche

| S3-Pfad | Inhalt |
| --- | --- |
| `raw/batches/<id>/` | Drei Original-CSV-Dateien und zuletzt ready.json |
| `snapshots/<run-id>/curated/customers/ und snapshots/<run-id>/curated/accounts/` | Gültige Referenzdaten als Parquet |
| `snapshots/<run-id>/curated/transactions/transaction_date=<tag>/` | Gültige Transaktionen als Parquet |
| `snapshots/<run-id>/rejected/<datensatz>/` | Abgelehnte Zeilen als JSON mit Gründen |
| `snapshots/<run-id>/dataset_rework_report.json` | Nacharbeitsbericht für Kunden, Konten und Transaktionen |
| `snapshots/<run-id>/comparison/csv/` | Dieselben gültigen Transaktionen als CSV |
| `runs/<run-id>/metrics.json` | Erfolgsquittung des konkreten Glue-Laufs |
| `submissions/<id>.json` | Dauerhafte bedingte S3-Paketsperre gegen doppelte automatische Starts |
| published/current.json | Einzelner Verweis auf den vollständigen veröffentlichten Snapshot |
| `releases/<hash>/` | Zusammengehöriges Glue-Skript und Python-Bibliothek |
| monitor/notified/ | Quittungen der Meldungen zu ERROR und EXPIRED |
| athena-results/ | Temporäre Abfrageergebnisse |

Jeder Lauf erzeugt eine eigene unveränderte Snapshot-Datenbank und eigene Dateien. Erst ein vollständiger Stand wird über `published/current.json` ausgewählt. Alte Snapshots werden nicht automatisch gelöscht; ihre Bereinigung ist eine bewusste Betriebsaufgabe. S3-Versionierung erhält zusätzlich ältere Objektversionen vorübergehend; diese nicht aktuellen Objektversionen werden nach 30 Tagen gelöscht. Siehe [Betrieb](operations.md).

Neue Snapshot-Kataloge ergänzen fehlende Kundenspalten `email` und `tel` aus dem ETL-Release als `string`, auch wenn die Terraform-Schemavorlage noch den älteren Stand enthält. Vorhandene Snapshot-Kataloge und die Vorlage werden dabei nicht verändert. Ein bestehender falscher Datentyp für diese Felder stoppt den Lauf vor der Veröffentlichung.
