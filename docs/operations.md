# BankGuard betreiben

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
terraform -chdir=terraform plan -var="alert_email=DEINE_ADRESSE"
terraform -chdir=terraform apply -var="alert_email=DEINE_ADRESSE"
```

Bei Anmeldung mit `aws login` kann der hier gepinnte AWS-Provider 5.x die CLI-Sitzung nicht direkt übernehmen. Dafür aus dem Projektstamm `python scripts/aws_terraform.py plan -input=false` bzw. `python scripts/aws_terraform.py apply` verwenden. Der Helfer exportiert temporäre CLI-Zugangsdaten ausschließlich in die Prozessumgebung von Terraform; er speichert oder druckt sie nicht.

Die lokalen Werte stehen in der ignorierten Datei `terraform/demo.auto.tfvars`. Sie erhalten den bestätigten Alarmempfänger, das GitHub-Repository, den bereits vorhandenen OIDC-Provider und `github_deploy_branch = "feature/bankguard"`. Die Datei vor späteren Änderungen prüfen; ein Aufruf ohne diese Werte könnte optionale Ressourcen zur Löschung planen.

Die SNS-Abonnementmail muss bestätigt werden. Die Datei `terraform/.terraform.lock.hcl` gehört ins Repository. Zugangsdaten und Terraform-State gehören nicht hinein.

## 3. Zusammengehörige Dateien hochladen

```bash
BUCKET=$(terraform -chdir=terraform output -raw bucket_name)
python scripts/upload_dataset.py "$BUCKET" data
```

Das Skript prüft zuerst alle drei lokalen Dateien. Es lädt sie unter `raw/batches/<eindeutige-id>/` hoch und schreibt erst danach `ready.json`. Die Freigabedatei startet Lambda. Dateien eines freigegebenen Pakets dürfen nicht verändert und Paket-IDs nicht wiederverwendet werden. Neue Daten bekommen immer eine neue Paket-ID.

Lambda prüft die Dateien und sucht das Paket in der Glue-Laufhistorie, bevor es einen neuen Lauf einreicht. Lambda verwendet die gemeinsame Nebenläufigkeitskapazität des Kontos, damit auch Konten ohne verfügbare reservierbare Kapazität unterstützt werden. Gleichzeitige doppelte Zustellungen können dabei doppelte Läufe einreichen. Glue nimmt weitere Pakete in seine Warteschlange auf und führt höchstens einen Lauf gleichzeitig aus. Die Deduplizierung ist auf die von Glue vorgehaltene Historie begrenzt (365 Tage); dies ist keine unbegrenzte Exactly-once-Garantie. Glue bestimmt die Reihenfolge wartender Läufe. `published/current.json` zeigt den zuletzt vollständig veröffentlichten Snapshot.

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

Airflow prüft die Quittung seiner konkreten Glue-Run-ID und verwendet deren Snapshot-Datenbank. Auch null gültige Transaktionen sind ein zulässiges Ergebnis. Alte Snapshot-Daten und Kataloge bleiben erhalten, bis sie bewusst bereinigt werden; Speicher und Katalogobjekte wachsen daher mit der Zahl der Läufe.

CloudWatch enthält strukturierte Logs und Kennzahlen. EventBridge meldet `FAILED`, `TIMEOUT` und `STOPPED`. Eine zusätzliche Lambda-Funktion prüft alle fünf Minuten die Glue-Historie auf `ERROR` und `EXPIRED`, sendet eine SNS-Meldung und speichert deren Quittung unter `monitor/notified/`. Fehlgeschlagene SNS-Sendungen werden nicht als zugestellt markiert; nach erfolgreichem Versand kann bei einer unterbrochenen Quittungsschreibung eine doppelte Meldung auftreten. Ein CloudWatch-Alarm meldet Fehler des Prüflaufs selbst. Die Alarmzustellung benötigt ein bestätigtes SNS-Abonnement.

Die Datenqualitätswarnung verwendet standardmäßig eine Grenze von 90 Prozent. Ein Budgetalarm stoppt keine Ressourcen.

## 5. Athena und GitHub Actions

Die Workgroup kommt aus `terraform output athena_workgroup`. Die Abfragedatenbank kommt aus `scripts/current_snapshot.py`, nicht aus `terraform output glue_database`: Letztere ist nur die Schemavorlage. Die SQL-Dateien verwenden unqualifizierte Tabellennamen; im Athena-Editor zuerst die veröffentlichte Snapshot-Datenbank auswählen.

Für einen Formatvergleich siehe [Kostenschätzung](cost-estimate.md). Glue exportiert dieselben akzeptierten Transaktionen in beiden Formaten. Keine Rohdaten in den Vergleichsbereich kopieren.

CI führt Tests, Formatprüfung und Terraform-Validierung aus. Für den manuellen Deploy-Workflow `github_repository=OWNER/REPO` setzen und Terraform anwenden. `github_deploy_branch` bestimmt den einzigen freigegebenen Deployment-Branch (Standard `main`, für die aktuelle Demo `feature/bankguard`). Einen bereits vorhandenen OIDC-Provider über `github_oidc_provider_arn` wiederverwenden. GitHub verwendet für dieses Repository unveränderliche Owner-/Repository-IDs im Token. Den exakten Präfix mit `gh api repos/OWNER/REPO/actions/oidc/customization/sub --jq .sub_claim_prefix` abrufen und als `github_oidc_subject_prefix` setzen. Für diese Demo lautet er `repo:gholipourmarya@91275535/BankGuard-AWS-Data-Engineering@1434311270`. Die Trust Policy vergleicht diesen Präfix plus den ausgewählten Branch exakt; sie verwendet keine Wildcards. [GitHub-Dokumentation](https://docs.github.com/en/actions/reference/security/oidc#immutable-subject-claims). Danach GitHub-Repository-Variablen `AWS_DEPLOY_ROLE_ARN`, `AWS_REGION`, `BANKGUARD_BUCKET` und `GLUE_JOB_NAME` eintragen. Der Workflow lädt Skript und Bibliothek unter dieselbe Inhalts-Hash-ID nach `releases/<hash>/` hoch. Erst nach beiden Uploads schaltet ein einzelner Glue-UpdateJob-Aufruf beide URIs gemeinsam um; Arbeitereinstellungen und übrige Jobparameter bleiben erhalten. Alte Versionen bleiben für laufende Jobs verfügbar. Gleichzeitige Deploy-Workflows werden serialisiert. Lambda und Infrastruktur werden durch Terraform aktualisiert. Terraform behält den vom Workflow gewählten Code-Stand bei. Ein optionaler Start durch den Workflow verwendet den manuellen Eingabebereich `raw/`.

## 6. Optionales Airflow und MWAA

MWAA ist standardmäßig ausgeschaltet. `enable_mwaa=true` erzeugt auch VPC und NAT-Gateway. Diese Ressourcen verursachen Kosten, solange sie existieren. Terraform liefert Datenbank, Bucket, Glue-Job und Workgroup als `bankguard_config.py` aus.

Der manuelle DAG erwartet drei Dateien direkt unter `raw/`; den automatischen Paket-Upload nicht parallel für denselben Demo-Lauf verwenden. Selbst betriebenes Airflow benötigt für `airflow/requirements.txt` eine separate Python-3.12-Umgebung. Diese optionalen Pakete nicht in die lokale BankGuard-Umgebung mit Python 3.14 installieren. Die Airflow-Variablen heißen `bankguard_bucket`, `bankguard_glue_job`, `bankguard_workgroup` und `bankguard_database`. `aws_default` muss AWS-Zugriff besitzen. Für MWAA gelten dessen eigene Paket-Constraints.

Nach der Demo MWAA wieder mit `enable_mwaa=false` entfernen oder das gesamte Projekt abbauen.

## 7. Aufräumen

Zuerst aktive Glue-Läufe stoppen oder auslaufen lassen. Die zur Laufzeit erzeugten Snapshot-Datenbanken sind nicht im Terraform-State enthalten. Vor `destroy` können sie mit dem folgenden Skript aufgelistet und ausdrücklich gelöscht werden:

```bash
TEMPLATE_DB=$(terraform -chdir=terraform output -raw glue_database)
python scripts/cleanup_snapshot_catalogs.py "$BUCKET" "$TEMPLATE_DB" --include-published
# Erst nach Prüfung der Liste löschen; dies beendet auch die Veröffentlichung:
python scripts/cleanup_snapshot_catalogs.py "$BUCKET" "$TEMPLATE_DB" --delete --include-published
terraform -chdir=terraform destroy
```

Ohne `--delete` wird nichts entfernt. Ohne `--include-published` bleibt die aktuell veröffentlichte Datenbank erhalten. Das Skript entfernt Kataloge; die Snapshot-Dateien werden beim anschließenden Terraform-Destroy des Buckets entfernt.

Die Demo verwendet `force_destroy`: Bucket-Inhalte und Versionen werden mitgelöscht. Vorher gewünschte Nachweise lokal sichern. Der KMS-Schlüssel bleibt bis zum Ende seiner Löschwartefrist sichtbar.

## Verifizierungsstand vom 28.10.2026

113 lokale Tests und der Docker-Durchlauf bestehen. Terraform wurde validiert und angewendet. Der Paket-Upload `172f45007c524d918708385d5ef7d086` startete automatisch den erfolgreichen Glue-Lauf `jr_3f971bd8d44b715be6f12631ea9ca7433b16dd89f37fb71b102fd237b0d51b8c`. Athena bestätigte 47.500 gültige und 3.500 abgelehnte Transaktionen, die gültigen Mengen pro Herkunftssystem sowie alle vorhandenen SQL-Berichte.

Ein absichtlich fehlender Eingabepfad erzeugte den Lauf `jr_f3bd319fb8a0cbd758b3f174b3b7af817209cad2d46969bf9b311802e01fb91b` mit `FAILED/NoSuchKey`. Der veröffentlichte Snapshot blieb exakt unverändert. Im zugehörigen Zeitfenster meldeten EventBridge eine Auslösung und SNS eine zugestellte Benachrichtigung. Dies bestätigt die AWS-Zustellmetrik; das persönliche Postfach wurde nicht eingesehen. Der periodische Laufprüfer wurde erfolgreich aufgerufen (`notified: []`); echte `ERROR`-/`EXPIRED`-Zustände wurden nicht erzeugt.

S3-Verschlüsselung mit KMS, Versionierung und alle vier Public-Access-Block-Schalter wurden direkt abgefragt. [Echte Abfrageergebnisse](evidence/2026-10-28/aws-verification.json), [Fehlerlauf](evidence/2026-10-28/failure-run.json), [SNS-Zustellung](evidence/2026-10-28/sns-delivery.json) und [CloudWatch-Diagramm](evidence/2026-10-28/cloudwatch-records.png) sind gesichert. Das Diagramm ist ein AWS-API-Export, kein Konsolen-Screenshot.

Eine erneute Prüfung des veröffentlichten Snapshots ist möglich mit:

```bash
python scripts/verify_aws.py "$BUCKET" --out build/aws-verification.json
```

Das Skript startet keine Glue-Jobs, führt aber kostenpflichtige Athena-Abfragen aus. Es löst den Snapshot einmal auf, prüft den erfolgreichen Glue-Zustand und die Anzahlen, führt die SQL-Berichte aus und vergleicht CSV und Parquet für einen tatsächlich vorhandenen Tag. Query-IDs, SQL, Ergebnisse und Laufzeiten landen in der JSON-Datei.

Airflow/MWAA bleibt optional und wurde nicht bereitgestellt. Die Demo-Ressourcen bleiben für die Präsentation bestehen und verursachen weiter Kosten. Eine Löschung wurde nicht ausgeführt; die Anleitung in Abschnitt 7 ist vorbereitet.


GitHub-Nachweise: [CI](https://github.com/gholipourmarya/BankGuard-AWS-Data-Engineering/actions/runs/40662802001) und [Deployment, erfolgreicher Versuch 2](https://github.com/gholipourmarya/BankGuard-AWS-Data-Engineering/actions/runs/40739499524). Beide liefen auf dem vorhandenen Remote-Commit `091d4363f37d1007d8559e7544a9e02ff760d81c` von `feature/bankguard`. Der ältere `main`-Workflow scheiterte bereits vor der AWS-Anmeldung, weil dort pytest fehlt. Der Projektbranch enthält diese Korrektur. Es wurde kein Merge nach `main` durchgeführt.

Nach dem GitHub-Test wurde der lokal geprüfte ETL-Release `66b30c771db345deb4ab9202d86f0fe6ded64f810279d303d4931b388fe9fa14` wieder aktiviert. Dieser enthält den korrigierten CSV-Stream-Zugriff aus dem erfolgreichen AWS-Test. Der nächste Deployment-Lauf muss den aktuellen, geprüften Code aus dem Projektbranch verwenden. Die ersten oben verlinkten GitHub-Läufe beziehen sich auf den zuvor vorhandenen Remote-Commit; der zusätzliche abschließende Lauf wird in GitHub Actions protokolliert. [Kontext und Release-ID](evidence/2026-10-28/verification-context.json).
