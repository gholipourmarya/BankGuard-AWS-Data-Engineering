# BankGuard betreiben

**Betriebsstatus nach dem Abbau am 04.11.2026:** Nach den erfolgreichen Tests wurden alle 73 Terraform-Ressourcen und drei zusätzliche Snapshot-Kataloge entfernt; der State ist leer. Der KMS-Schlüssel ist zur Löschung vorgemerkt. CloudWatch-Logs außerhalb des State und der gemeinsam genutzte GitHub-OIDC-Provider bleiben erhalten. [Abbau-Nachweis](evidence/2026-11-04/terraform-destroy/summary.json). Die Prüfungen vom 04.11.2026 beschreiben den vorherigen Teststand. Betriebsbefehle aus dem Projektverzeichnis ausführen.

## 1. Lokal starten

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m src.generate_data
python -m src.transform data output
python -m pytest -q
```

`output/metrics.json` enthält die Bilanz. Für jede Transaktion gilt: Eingang = akzeptiert + abgelehnt. Auch leere Ergebnisse überschreiben vorherige Ausgaben. Negative Generatorzahlen werden abgelehnt.

## 2. Infrastruktur bereitstellen

AWS CLI mit gültiger Anmeldung, Terraform ab 1.5 und Rechte zum Anlegen der im Terraform-Verzeichnis beschriebenen Ressourcen werden benötigt. Es werden ausschließlich synthetische Daten verwendet.

```bash
terraform -chdir=terraform init
terraform -chdir=terraform fmt -check -recursive
terraform -chdir=terraform validate
python scripts/aws_terraform.py plan -var="alert_email=DEINE_ADRESSE"
python scripts/aws_terraform.py apply -var="alert_email=DEINE_ADRESSE"
```

Bei Anmeldung mit `aws login` kann der hier gepinnte AWS-Provider 5.x die CLI-Sitzung nicht direkt übernehmen. Dafür aus dem Projektstamm `python scripts/aws_terraform.py plan -input=false` bzw. `python scripts/aws_terraform.py apply` verwenden. Der Helfer verwendet ein temporäres Profil mit `credential_process`, das Zugangsdaten bei Bedarf erneut über die angemeldete AWS CLI abruft. Dadurch können auch lange MWAA-Erstellungen die kurzlebigen Tokens erneuern. Die temporäre Datei enthält nur den Aufruf und keine Zugangsdaten; das ursprüngliche AWS-Profil bleibt unverändert. Bereits explizit gesetzte Umgebungszugangsdaten verwendet Terraform direkt.

Die lokalen Werte stehen in der ignorierten Datei `terraform/demo.auto.tfvars`. Sie erhalten den bestätigten Alarmempfänger, das GitHub-Repository, den bereits vorhandenen OIDC-Provider und `github_deploy_branch = "feature/bankguard"`. Die Datei vor späteren Änderungen prüfen; ein Aufruf ohne diese Werte könnte optionale Ressourcen zur Löschung planen.

Die SNS-Abonnementmail muss bestätigt werden. Die Datei `terraform/.terraform.lock.hcl` gehört ins Repository. Zugangsdaten und Terraform-State gehören nicht hinein.

## 3. Zusammengehörige Dateien hochladen

```bash
BUCKET=$(terraform -chdir=terraform output -raw bucket_name)
python scripts/upload_dataset.py "$BUCKET" data
```

Das Skript prüft zuerst alle drei lokalen Dateien. Es lädt sie unter `raw/batches/<eindeutige-id>/` hoch und schreibt erst danach `ready.json`. Die Freigabedatei startet Lambda. Dateien eines freigegebenen Pakets dürfen nicht verändert und Paket-IDs nicht wiederverwendet werden. Neue Daten bekommen immer eine neue Paket-ID.

Lambda prüft die Dateien und die Glue-Laufhistorie. Vor jedem neuen Start legt es mit einem bedingten S3-Schreibzugriff (`If-None-Match: *`) eine dauerhafte Sperre unter `submissions/<Paket-ID>.json` an. Nur der Aufruf, der diese Sperre erfolgreich erstellt, darf Glue starten. Gleichzeitige Zustellungen können dadurch keinen zweiten Start auslösen. Die Sperren haben keinen automatischen Ablauf und dürfen nicht gelöscht werden, solange automatische Zustellungen möglich sind. Das schützt auch nach Ablauf der Glue-Historie vor automatischer Neueinreichung. Bereits vor Einführung der Sperren eingereichte Pakete werden nur anhand der vorhandenen Glue-Historie erkannt; alte Marker daher nicht erneut zustellen. Lambda verwendet die gemeinsame Nebenläufigkeitskapazität des Kontos. Glue nimmt weitere Pakete in seine Warteschlange auf und führt höchstens einen Lauf gleichzeitig aus. Glue bestimmt die Reihenfolge wartender Läufe. `published/current.json` zeigt den zuletzt vollständig veröffentlichten Snapshot.

Wird ein Aufruf nach dem Anlegen der Sperre unterbrochen oder ist die Antwort auf den Glue-Start unklar, bleibt die Sperre bestehen. Findet eine Wiederholung keinen passenden Lauf in der Historie, schlägt sie ausdrücklich fehl und löst nach ausgeschöpften Wiederholungen den bestehenden SNS-Fehlerweg aus. Vor einem bewussten manuellen Neustart zuerst die Glue-Historie prüfen und sicherstellen, dass der ursprüngliche Lambda-Aufruf beendet ist und keine weiteren automatischen Wiederholungen mehr anstehen. Die Sperre bleibt auch beim manuellen Neustart erhalten. Das bevorzugt die Vermeidung doppelter Starts gegenüber einer automatischen Wiederholung bei unklarem Ausgang.

Der Glue-Client im Lambda-Trigger verwendet ausdrücklich `total_max_attempts=1`, fünf Sekunden Verbindungs- und zehn Sekunden Antwort-Timeout. Damit wiederholt das SDK einen möglicherweise bereits angenommenen Start bei verlorener Antwort nicht intern. Die äußere Lambda-Wiederholung prüft erneut Historie und Sperre. Diese Einstellung überschreibt auch großzügigere Wiederholungsvorgaben aus der Umgebung. Siehe [boto3-Wiederholungskonfiguration](https://docs.aws.amazon.com/boto3/latest/guide/retries.html).

Fehlgeschlagene Lambda-Aufrufe werden wiederholt und nach ausgeschöpften Wiederholungen an das SNS-Alarmthema gesendet. Glue-Warteschlangen sind zeitlich begrenzt; `EXPIRED`, `FAILED` oder `TIMEOUT` erfordern eine Prüfung und gegebenenfalls einen bewussten Neustart. Ein bereits eingereichtes Paket wird durch erneutes Hochladen seines Markers nicht automatisch wiederholt.

Ein einzelnes Paket bewusst erneut verarbeiten:

```bash
JOB=$(terraform -chdir=terraform output -raw glue_job_name)
aws glue start-job-run --job-name "$JOB" --job-run-queuing-enabled --arguments '{"--input_prefix":"raw/batches/PAKET_ID"}'
```

Der explizite Neustart umgeht die Deduplizierung absichtlich. Auch ein manueller Lauf mit drei Dateien direkt unter `raw/` bleibt möglich; diese Dateien müssen vollständig hochgeladen sein, bevor der Lauf gestartet wird.

## 4. Ergebnisse und Fehler prüfen

Glue schreibt alle Ausgaben unter `snapshots/<Glue-Run-ID>/`: gültige Daten, Ablehnungen und Vergleichs-CSV. Für jeden Lauf wird eine eigene Katalogdatenbank `<Vorlagen-Datenbank>_<Glue-Run-ID>` erzeugt. Bereits vorhandene Lauf-IDs werden nicht erneut beschrieben. Fehler während der Vorbereitung beschädigen dadurch keinen früheren Datenbestand.

Erst nach Ausgabe, Katalogregistrierung und Glue-Commit schreibt der Job `runs/<Glue-Run-ID>/metrics.json` und stellt danach das einzelne S3-Objekt `published/current.json` um. Dieser Verweis enthält die veröffentlichte Datenbank und die Bilanz. Leser lösen ihn einmal auf und verwenden diese Datenbank für alle zusammengehörigen Abfragen. Ein paralleler neuer Lauf verändert diese Datenbank nicht. Die durch Terraform erstellte Datenbank dient ausschließlich als Schemavorlage.

```bash
python scripts/current_snapshot.py "$BUCKET"
python scripts/current_snapshot.py "$BUCKET" --database-only
```

`scripts/verify_aws.py` gleicht vor der ersten Athena-Abfrage die Lauf-ID und den Jobnamen mit der Glue-Antwort ab. Zusätzlich müssen Bucket, Eingabepfad, Snapshot-Datenbank und Datenpfad zum Lauf passen. Da `GetJobRun` nur die Laufparameter liefert, ergänzt die Prüfung die aktuelle Jobdefinition in der AWS-Reihenfolge: `DefaultArguments`, Lauf-`Arguments`, zuletzt `NonOverridableArguments` ([AWS-Parameterregeln](https://docs.aws.amazon.com/glue/latest/dg/aws-glue-programming-etl-glue-arguments.html)). Für historische Läufe setzt dieser Abgleich unveränderte relevante Jobvorgaben voraus; nach Änderungen müssen die damaligen Vorgaben anhand gesicherter Nachweise separat geprüft werden. Ein Widerspruch wird nicht als erfolgreiche Verifikation gespeichert.

Airflow prüft die Quittung seiner konkreten Glue-Run-ID und verwendet deren Snapshot-Datenbank. Auch null gültige Transaktionen sind ein zulässiges Ergebnis. Alte Snapshot-Daten und Kataloge bleiben erhalten, bis sie bewusst bereinigt werden; Speicher und Katalogobjekte wachsen daher mit der Zahl der Läufe.

CloudWatch enthält strukturierte Logs und Kennzahlen. EventBridge meldet `FAILED`, `TIMEOUT` und `STOPPED`. Eine zusätzliche Lambda-Funktion prüft alle fünf Minuten die Glue-Historie auf `ERROR` und `EXPIRED`, sendet eine SNS-Meldung und speichert deren Quittung unter `monitor/notified/`. Fehlgeschlagene SNS-Sendungen werden nicht als zugestellt markiert; nach erfolgreichem Versand kann bei einer unterbrochenen Quittungsschreibung eine doppelte Meldung auftreten. Ein CloudWatch-Alarm meldet Fehler des Prüflaufs selbst. Die Alarmzustellung benötigt ein bestätigtes SNS-Abonnement.

Die Datenqualitätswarnung verwendet standardmäßig eine Grenze von 90 Prozent. Neue ETL-Releases senden getrennte CloudWatch-Kennzahlen für Kunden, Konten, Transaktionen und den Gesamtstand (`Dataset` = `customers`, `accounts`, `transactions`, `overall`, jeweils zusammen mit `JobName`). Jede Datei hat einen eigenen Qualitätsalarm, zusätzlich besteht der Gesamtalarm. Gute Referenzdaten können deshalb keine schlechte Transaktionsquote verdecken. Leere Dateien senden keine Erfolgsquote. Die obersten Quittungszähler bleiben Transaktionswerte; `by_dataset` und `overall` enthalten die weiteren Kennzahlen. Kunden- und Kontenfehler stehen außerdem unter `snapshots/<run-id>/dataset_rework_report.json`. Die geänderten Alarme und das Dashboard benötigen Terraform-Apply, der Glue-Code ein ETL-Deployment. Alte dimensionslose Metriken bleiben historische Daten und werden von den neuen Alarmen nicht ausgewertet. Ein Budgetalarm stoppt keine Ressourcen.

## 5. Athena und GitHub Actions

Das ETL-Release ergänzt `email` und `tel` beim Anlegen neuer Snapshot-Kataloge auch dann, wenn die Terraform-Schemavorlage diese Spalten noch nicht enthält. Dafür ist kein separates Terraform-Apply nötig. Bestehende Snapshots bleiben unverändert. Alte Kundendateien mit nur `customer_id,name,country` sind weiterhin zulässig; neue Kontaktwerte werden vor der Ausgabe geprüft.

Die Workgroup kommt aus `terraform -chdir=terraform output -raw athena_workgroup`. Die Abfragedatenbank kommt aus `scripts/current_snapshot.py`, nicht aus `terraform -chdir=terraform output -raw glue_database`: Letztere ist nur die Schemavorlage. Die SQL-Dateien verwenden unqualifizierte Tabellennamen; im Athena-Editor zuerst die veröffentlichte Snapshot-Datenbank auswählen.

Für einen Formatvergleich siehe [Kostenschätzung](cost-estimate.md). Glue exportiert dieselben akzeptierten Transaktionen in beiden Formaten. Keine Rohdaten in den Vergleichsbereich kopieren.

CI führt Tests, Formatprüfung und Terraform-Validierung aus. Für den manuellen Deploy-Workflow `github_repository=OWNER/REPO` setzen und Terraform anwenden. `github_deploy_branch` bestimmt den einzigen freigegebenen Deployment-Branch (Standard `main`, für die aktuelle Demo `feature/bankguard`). Einen bereits vorhandenen OIDC-Provider über `github_oidc_provider_arn` wiederverwenden. GitHub verwendet für dieses Repository unveränderliche Owner-/Repository-IDs im Token. Den exakten Präfix mit `gh api repos/OWNER/REPO/actions/oidc/customization/sub --jq .sub_claim_prefix` abrufen und als `github_oidc_subject_prefix` setzen. Für diese Demo lautet er `repo:gholipourmarya@91275535/BankGuard-AWS-Data-Engineering@1434311270`. Die Trust Policy vergleicht diesen Präfix plus den ausgewählten Branch exakt; sie verwendet keine Wildcards. [GitHub-Dokumentation](https://docs.github.com/en/actions/reference/security/oidc#immutable-subject-claims). Danach GitHub-Repository-Variablen `AWS_DEPLOY_ROLE_ARN`, `AWS_REGION`, `BANKGUARD_BUCKET` und `GLUE_JOB_NAME` eintragen. Der Workflow lädt Skript und Bibliothek unter dieselbe Inhalts-Hash-ID nach `releases/<hash>/` hoch. Erst nach beiden Uploads schaltet ein einzelner Glue-UpdateJob-Aufruf beide URIs gemeinsam um; Arbeitereinstellungen und übrige Jobparameter bleiben erhalten. Alte Versionen bleiben für laufende Jobs verfügbar. Gleichzeitige Deploy-Workflows werden serialisiert. Lambda und Infrastruktur werden durch Terraform aktualisiert. Terraform behält den vom Workflow gewählten Code-Stand bei. Ein optionaler Start durch den Workflow verwendet den manuellen Eingabebereich `raw/`.

## 6. Optionales Airflow und MWAA

MWAA ist standardmäßig ausgeschaltet. `enable_mwaa=true` erzeugt auch VPC und NAT-Gateway. Diese Ressourcen verursachen Kosten, solange sie existieren. Terraform liefert Datenbank, Bucket, Glue-Job und Workgroup als `bankguard_config.py` aus und lädt die gemeinsame Datei `src/validate.py` als `dags/bankguard_validation.py` hoch. Airflow verwendet daraus dieselbe Zählerprüfung wie Glue und die lokalen Receipt-Werkzeuge. Bei selbst betriebenem Airflow diese Hilfsdatei ebenfalls neben dem DAG bereitstellen und bei Änderungen gemeinsam aktualisieren; beim Entwickeln mit dem Projektstamm im Python-Suchpfad wird `src.validate` verwendet.

Der manuelle DAG erwartet drei Dateien direkt unter `raw/`; den automatischen Paket-Upload nicht parallel für denselben Demo-Lauf verwenden. Selbst betriebenes Airflow benötigt für `airflow/requirements.txt` eine separate Python-3.12-Umgebung. Diese optionalen Pakete nicht in die lokale BankGuard-Umgebung mit Python 3.14 installieren:

```bash
python3.12 -m venv .venv-airflow
.venv-airflow/bin/python -m pip install -r airflow/requirements.txt
.venv-airflow/bin/python -m pip check
```

`airflow/requirements.lock` enthält eigene feste Constraints für Airflow 2.11.0 und den Amazon-Provider 9.9.0 auf Linux mit Python 3.12. Sie wurden aus einer getrennten Installation erstellt: zuerst Airflow mit den [offiziellen Constraints](https://raw.githubusercontent.com/apache/airflow/constraints-2.11.0/constraints-3.12.txt), anschließend der Amazon-Provider 9.9.0 bei weiterhin festgelegtem Airflow 2.11.0. Die offiziellen Constraints legen den Amazon-Provider auf 9.8.0 fest; deshalb wird 9.9.0 im zweiten Schritt ohne diese Constraints installiert, wie in der [Apache-Anleitung](https://airflow.apache.org/docs/apache-airflow/2.11.0/installation/installing-from-pypi.html#managing-providers-separately-from-airflow-core) beschrieben. Der danach geprüfte Paketstand ist in den eigenen Constraints festgehalten. Neue Versionen nur in einer frischen separaten Umgebung installieren und nach `pip check` sowie einem DAG-Import in die Constraints übernehmen.

Die Airflow-Variablen heißen `bankguard_bucket`, `bankguard_glue_job`, `bankguard_workgroup` und `bankguard_database`. `aws_default` muss AWS-Zugriff besitzen. Für MWAA gelten dessen eigene Paket-Constraints; die lokale Lockdatei ist nicht für MWAA bestimmt.

### Im Browser öffnen und den DAG starten

Die am 04.11.2026 erneut bereitgestellte Umgebung heißt `bankguard-airflow` in `eu-central-1`. In der AWS-MWAA-Konsole die Umgebung auswählen und **Open Airflow UI** öffnen. `localhost:8080` funktioniert nur mit einem separat gestarteten lokalen Airflow-Webserver.

Für diesen manuellen DAG die Dateien direkt unter `raw/` hochladen. Der automatische Paket-Uploader unter `raw/batches/` bedient einen anderen Eingabepfad:

```bash
BUCKET=$(terraform -chdir=terraform output -raw bucket_name)
aws s3 cp data/customers.csv "s3://$BUCKET/raw/customers.csv"
aws s3 cp data/accounts.csv "s3://$BUCKET/raw/accounts.csv"
aws s3 cp data/transactions.csv "s3://$BUCKET/raw/transactions.csv"
```

Während eines DAG-Laufs die Dateien nicht verändern. Danach in Airflow `bankguard_pipeline` aktivieren und **Trigger DAG** wählen. Alle vier Schritte müssen erfolgreich enden: `check_inputs → start_glue_job → verify_outputs → run_athena_query`.

Wiederholtes `Poking for key .../raw/customers.csv` bedeutet, dass der Sensor auf die Datei wartet. Die drei CSV-Dateien an den oben genannten Pfad hochladen; ist der Sensor bereits nach 15 Minuten fehlgeschlagen, nach Prüfung aktiver Glue-Läufe den fehlgeschlagenen Task mit **Clear** erneut ausführen oder einen neuen DAG-Lauf starten. Eine `DeprecatedImportWarning` allein ist kein fehlgeschlagener Task.

### Nachgewiesener MWAA-Lauf am 28.10.2026

Die Umgebung war beim Test `AVAILABLE` und verwendete Airflow **3.3.1** mit Klasse **mw1.micro**. Die Airflow-API bestätigt für `manual__2026-10-28T19:33:59.642161+00:00` den Gesamtstatus **success**. Start: **20:34:00**, Ende: **20:36:58**, jeweils Europe/Berlin (UTC+1); Gesamtdauer **178,353 Sekunden**, einschließlich Scheduling zwischen den Tasks.

| Task | Operator | Status | Dauer laut Airflow-API |
| --- | --- | --- | --- |
| `check_inputs` | S3KeySensor | success | 1,242 s |
| `start_glue_job` | GlueJobOperator | success | 140,777 s |
| `verify_outputs` | PythonOperator | success | 0,892 s |
| `run_athena_query` | AthenaOperator | success | 31,240 s |

Die Task-Dauer des Glue-Operators umfasst den Aufruf und das Warten auf Glue; sie ist nicht mit der reinen Glue-Ausführungszeit gleichzusetzen. Die früher dokumentierten 135 Sekunden gehören zu einem separaten direkten AWS-Lauf.

Nachweise: [MWAA-Umgebung](evidence/2026-10-28/airflow-environment.json), [DAG-Läufe](evidence/2026-10-28/airflow-runs.json) und [Task-Ergebnisse](evidence/2026-10-28/airflow-tasks.json). Vorherige Sensor-Läufe scheiterten am fehlenden direkten Eingabepfad; nach dem Upload endete der neue Lauf erfolgreich. Diese Nachweise gehören zur Bereitstellung vom 28.10.2026; die neue Bereitstellung vom 04.11.2026 hat eigene Nachweise.

Nach der Demo MWAA wieder mit `enable_mwaa=false` entfernen oder das gesamte Projekt abbauen.

## 7. Aufräumen

Zuerst aktive Glue-Läufe stoppen oder auslaufen lassen. Die zur Laufzeit erzeugten Snapshot-Datenbanken sind nicht im Terraform-State enthalten. Vor `destroy` können sie mit dem folgenden Skript aufgelistet und ausdrücklich gelöscht werden:

```bash
TEMPLATE_DB=$(terraform -chdir=terraform output -raw glue_database)
python scripts/cleanup_snapshot_catalogs.py "$BUCKET" "$TEMPLATE_DB" --include-published
# Erst nach Prüfung der Liste löschen; dies beendet auch die Veröffentlichung:
python scripts/cleanup_snapshot_catalogs.py "$BUCKET" "$TEMPLATE_DB" --delete --include-published
python scripts/aws_terraform.py destroy
```

Ohne `--delete` wird nichts entfernt. Ohne `--include-published` bleibt die aktuell veröffentlichte Datenbank erhalten. Das Skript entfernt Kataloge; die Snapshot-Dateien werden beim anschließenden Terraform-Destroy des Buckets entfernt.

Die Demo verwendet `force_destroy`: Bucket-Inhalte und Versionen werden mitgelöscht. Vorher gewünschte Nachweise lokal sichern. Der KMS-Schlüssel bleibt bis zum Ende seiner Löschwartefrist sichtbar.

## Verifizierungsstand vom 28.10.2026

Damals bestanden 113 lokale Tests und der Docker-Durchlauf. Terraform wurde validiert und angewendet. Der Paket-Upload `172f45007c524d918708385d5ef7d086` startete automatisch den erfolgreichen Glue-Lauf `jr_3f971bd8d44b715be6f12631ea9ca7433b16dd89f37fb71b102fd237b0d51b8c`. Athena bestätigte 47.500 gültige und 3.500 abgelehnte Transaktionen, die gültigen Mengen pro Herkunftssystem sowie alle vorhandenen SQL-Berichte.

Ein absichtlich fehlender Eingabepfad erzeugte den Lauf `jr_f3bd319fb8a0cbd758b3f174b3b7af817209cad2d46969bf9b311802e01fb91b` mit `FAILED/NoSuchKey`. Der veröffentlichte Snapshot blieb exakt unverändert. Im zugehörigen Zeitfenster meldeten EventBridge eine Auslösung und SNS eine zugestellte Benachrichtigung. Dies bestätigt die AWS-Zustellmetrik; das persönliche Postfach wurde nicht eingesehen. Der periodische Laufprüfer wurde erfolgreich aufgerufen (`notified: []`); echte `ERROR`-/`EXPIRED`-Zustände wurden nicht erzeugt.

S3-Verschlüsselung mit KMS, Versionierung und alle vier Public-Access-Block-Schalter wurden direkt abgefragt. [Echte Abfrageergebnisse](evidence/2026-10-28/aws-verification.json), [Fehlerlauf](evidence/2026-10-28/failure-run.json), [SNS-Zustellung](evidence/2026-10-28/sns-delivery.json) und [CloudWatch-Diagramm](evidence/2026-10-28/cloudwatch-records.png) sind gesichert. Das Diagramm ist ein AWS-API-Export, kein Konsolen-Screenshot.

Eine erneute Prüfung des veröffentlichten Snapshots ist möglich mit:

```bash
python scripts/verify_aws.py "$BUCKET" --out build/aws-verification.json
```

Das Skript startet keine Glue-Jobs, führt aber kostenpflichtige Athena-Abfragen aus. Es löst den Snapshot einmal auf, prüft den erfolgreichen Glue-Zustand und die Anzahlen, führt die SQL-Berichte aus und vergleicht CSV und Parquet für einen tatsächlich vorhandenen Tag. Bei null akzeptierten Transaktionen bleibt der Snapshot gültig; nur der Formatvergleich wird mit `comparison_status: skipped` und einer Begründung ausgelassen. Die gemeinsame Validierung fordert echte, nicht negative Ganzzahlen und `counts_consistent: true`. Sie berechnet die Bilanzen erneut und kontrolliert vorhandene Erfolgsquoten gegen die Zähler. Athena muss die akzeptierten und abgelehnten Transaktionsanzahlen pro Herkunftssystem bestätigen. Bei Quittungen mit `by_dataset` prüft Athena zusätzlich die akzeptierten Kunden und Konten; die abgelehnten Referenzdatensätze zählt das Skript direkt aus den JSON-Dateien unter dem festen Snapshot-Pfad. Abweichende Zähler oder ungültige JSON-Datensätze brechen die Prüfung ab. `counts_verified: true` gilt für die unter `verified_datasets` aufgeführten Dateien; bei älteren Quittungen ohne `by_dataset` steht dort nur `transactions`. Ursprüngliche Leerzeichen und fehlende Herkunftssystemwerte der abgelehnten Datensätze werden für diesen Vergleich wie in der Pipeline gruppiert. Query-IDs, SQL, Ergebnisse und Laufzeiten landen in der JSON-Datei.

Die frühere Airflow-/MWAA-Bereitstellung und alle vier Tasks wurden erfolgreich geprüft (siehe historische Nachweise in Abschnitt 6); diese Infrastruktur wurde am 28.10.2026 entfernt. Am 04.11.2026 wurde die Infrastruktur einschließlich MWAA erneut bereitgestellt, geprüft und anschließend wieder abgebaut. [Zwei erfolgreiche DAG-Läufe](evidence/2026-11-04/airflow-runs.json) und die [AWS-Prüfung aller drei Datensätze mit zwölf Athena-Abfragen](evidence/2026-11-04/aws-verification.json) dokumentieren den neuen Stand. Die SNS-Subscription ist bestätigt; die Alarmzustellung im neuen Deployment wurde noch nicht erneut getestet.


GitHub-Nachweise: [CI](https://github.com/gholipourmarya/BankGuard-AWS-Data-Engineering/actions/runs/40662802001) und [Deployment, erfolgreicher Versuch 2](https://github.com/gholipourmarya/BankGuard-AWS-Data-Engineering/actions/runs/40739499524). Beide liefen auf dem vorhandenen Remote-Commit `091d4363f37d1007d8559e7544a9e02ff760d81c` von `feature/bankguard`. Der ältere `main`-Workflow scheiterte bereits vor der AWS-Anmeldung, weil dort pytest fehlt. Der Projektbranch enthält diese Korrektur. Es wurde kein Merge nach `main` durchgeführt.

Nach dem GitHub-Test wurde der lokal geprüfte ETL-Release `66b30c771db345deb4ab9202d86f0fe6ded64f810279d303d4931b388fe9fa14` wieder aktiviert. Dieser enthält den korrigierten CSV-Stream-Zugriff aus dem erfolgreichen AWS-Test. Der nächste Deployment-Lauf muss den aktuellen, geprüften Code aus dem Projektbranch verwenden. Die ersten oben verlinkten GitHub-Läufe beziehen sich auf den zuvor vorhandenen Remote-Commit; der zusätzliche abschließende Lauf wird in GitHub Actions protokolliert. [Kontext und Release-ID](evidence/2026-10-28/verification-context.json).


Wenn Terraform während der MWAA-Erstellung `ExpiredTokenException` meldet, zuerst mit `aws mwaa get-environment --name bankguard-airflow --region eu-central-1` den echten Zustand prüfen. Die Erstellung kann trotz beendetem Terraform weiterlaufen. Keine parallelen Applies starten. Sobald die Umgebung `AVAILABLE` ist, mit dem aktualisierten Helfer zuerst `python scripts/aws_terraform.py plan -refresh-only` ausführen. Nach einem abgebrochenen Create kann Terraform die Umgebung als `tainted` markieren. Nur wenn AWS die erfolgreiche Erstellung mit `AVAILABLE` bestätigt, diese Markierung mit `terraform -chdir=terraform untaint 'aws_mwaa_environment.airflow[0]'` entfernen; ein normaler Apply würde sonst eine Neuerstellung planen. Steht die MWAA-Ressource bereits im Terraform-State, aktualisiert `python scripts/aws_terraform.py apply -refresh-only` nach Prüfung nur den lokalen State und die Outputs. Fehlt die vorhandene Umgebung im State, muss sie zunächst mit `python scripts/aws_terraform.py import 'aws_mwaa_environment.airflow[0]' bankguard-airflow` übernommen werden. Erst anschließend einen normalen Plan prüfen. Ein vollständiger Ablauf der AWS-Anmeldesitzung benötigt eine erneute Anmeldung; `credential_process` verlängert keine abgelaufene Gesamtsitzung.

## Historische Nachprüfung des ETL-Releases vom 28.10.2026

Die strengen Zähler- und Unicode-Prüfungen aus Commit `80af12bdfcd373b46651ac51bc7996a659913524` wurden am 28.10.2026 auch auf AWS bereitgestellt und geprüft. Der bei dieser Nachprüfung aktive Release war `bc662abf70e0f9b5bda599d051d2354fb22090dac63457b7c2ce285bfdfb5f93`. Terraform ergänzte `bankguard_validation.py` und aktualisierte den DAG: ein Objekt hinzugefügt, ein Objekt geändert, keine Ressourcen gelöscht. Der abschließende Plan meldet keine Änderungen. Der von der Airflow-API gelieferte DAG-Quelltext stimmt mit der lokalen Datei überein.

| Test | Eingang | Akzeptiert | Abgelehnt | Ergebnis |
| --- | --- | --- | --- | --- |
| Kleiner MWAA-Lauf mit Unicode-Steuerzeichen in der Transaktions-ID | 1 | 0 | 1 | DAG und alle vier Tasks erfolgreich; Glue 61 s; Athena-Prüfung erfolgreich; Formatvergleich begründet übersprungen |
| Vollständiger automatischer Paket-Upload mit zusätzlicher Unicode-Testzeile | 51.001 | 47.500 | 3.501 | Lambda startete Glue; Glue 127 s; Gesamt- und Herkunftssystembilanzen sowie CSV/Parquet bestätigt |

MWAA-Run: `manual__revalidation_80af12b_zero_20261028T204213Z`, Gesamtdauer **139,580 s**. Zugehöriger Glue-Run: `jr_708d1a9188178b8913369f298618cbe6cdf7e7891278af53eec3e8599f82ffe6`. Der vollständige Lauf ist `jr_6b9bc295becd15100fba83cb524011c1a6574b6ddd67178e14216fc6d1eb3d48` für Paket `33903d7dde414d2fa1061270acccd4bf`. Nach Abschluss zeigte `published/current.json` wieder auf den vollständigen Snapshot; auch die direkten `raw/`-Eingaben waren wieder vollständig. Diese S3-Objekte wurden beim anschließenden Infrastrukturabbau entfernt.

Beim Integrationstest zeigte sich zusätzlich ein Fehler beim Aufteilen von SQL-Berichten: Semikolons in Kommentaren wurden als Anweisungsende behandelt. Das Prüfskript ignoriert jetzt Kommentar-Semikolons und erhält Semikolons innerhalb von Zeichenketten oder Bezeichnern. Nach dieser Korrektur wurden beide vorhandenen Snapshots ohne erneuten Glue-Lauf erfolgreich geprüft. Für einen bereits abgelösten, noch vorhandenen Snapshot kann dessen feste Run-ID angegeben werden. Die historischen Snapshots vom 28.10.2026 wurden abgebaut und sind dafür nicht mehr verfügbar:

```bash
GLUE_RUN_ID="DEINE_NOCH_VORHANDENE_GLUE_RUN_ID"
python scripts/verify_aws.py "$BUCKET" --run-id "$GLUE_RUN_ID" --out build/run-verification.json
```

Diese Auswahl liest die unveränderte laufbezogene Quittung und prüft ihren Run-ID-Bezug, sämtliche Zähler und den erfolgreichen Glue-Zustand. Sie verändert den veröffentlichten Snapshot nicht.

[Zusammenfassung](evidence/2026-10-28/revalidation-80af12b/summary.json), [vollständige Prüfung](evidence/2026-10-28/revalidation-80af12b/full-verification.json), [Null-Ergebnis-Prüfung](evidence/2026-10-28/revalidation-80af12b/zero-verification.json), [MWAA-Tasks](evidence/2026-10-28/revalidation-80af12b/airflow-tasks.json), [Versionskontext](evidence/2026-10-28/revalidation-80af12b/context.json) und [CloudWatch-Diagramm](evidence/2026-10-28/revalidation-80af12b/cloudwatch-records.png) sind gesichert. Das Diagramm ist ein echter AWS-API-Export, kein Konsolen-Screenshot. Alte Screenshots dürfen nicht als Beleg für den neuen Stand umbenannt werden; ein neuer Browser-Screenshot muss den tatsächlichen Stand der neuen Bereitstellung zeigen.

Damals bestanden 243 lokale Tests. Die ergänzten lokalen Änderungen am Prüfskript (SQL-Aufteilung und feste Run-ID-Auswahl) sind noch nicht committed; sie wurden gegen die echten AWS-Snapshots ausgeführt. Das separat bereitgestellte ETL-/DAG-Paket stammt aus dem oben genannten Commit. Die Tests für negative oder widersprüchliche Metadaten sind Regressionstests ohne manipulierte Cloud-Quittungen; die beiden AWS-Läufe prüfen echte veröffentlichte Daten. MWAA, NAT-Gateway und die übrigen Terraform-Demo-Ressourcen wurden anschließend entfernt; siehe Abbau-Nachweis unten.


### Abgeschlossener Infrastrukturabbau am 28.10.2026

Alle 70 Terraform-Ressourcen wurden abgebaut; der abschließende State enthält keine verwalteten Ressourcen und keine Outputs. Zusätzlich wurden acht Snapshot-Katalogdatenbanken entfernt. AWS bestätigt MWAA, VPC, beide Buckets und Glue-Job als nicht vorhanden und das NAT-Gateway als gelöscht. Der KMS-Schlüssel ist für den 04.11.2026 um 22:27:59 MEZ zur Löschung vorgemerkt. CloudWatch-Logs außerhalb des State und der gemeinsam genutzte GitHub-OIDC-Provider bleiben erhalten. Die alten AWS-Snapshots sind nicht mehr abfragbar; Quellcode und lokale Nachweise bleiben erhalten.

[Abbau-Nachweis](evidence/2026-10-28/terraform-destroy/README.md) und [abschließende AWS-Abfragen](evidence/2026-10-28/terraform-destroy/final-aws-checks.json). Ein erneutes Terraform-Apply erstellt die Infrastruktur wieder; für einen neuen Live-Test müssen auch Daten und Release erneut hochgeladen werden.
