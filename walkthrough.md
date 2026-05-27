# Walkthrough: Web.de E-Mail Backup-Skript

Wir haben das Python-Skript zur automatischen Sicherung von E-Mails von mehreren Web.de-Accounts erfolgreich implementiert.

## Erstellte Dateien

1. **[backup_mails.py](file:///Users/robinwerner/MailStore/backup_mails.py)**: Das Hauptskript mit der Backup-Logik, Retry-Mechanismen, inkrementeller Synchronisation (unter Beachtung von `UIDVALIDITY`) und ordnerspezifischem UTF-7 Decoding.
2. **[config.json.template](file:///Users/robinwerner/MailStore/config.json.template)**: Eine Beispiel-Konfigurationsvorlage, die zeigt, wie Accounts eingetragen werden.

---

## Einrichtung & Ausführung

### 1. Konfiguration erstellen
Kopieren Sie die Vorlagendatei nach `config.json` und tragen Sie Ihre Web.de E-Mail-Adressen und Passwörter (oder App-Passwörter) ein:

```bash
cp config.json.template config.json
```

Beispiel für eine ausgefüllte `config.json`:
```json
{
  "accounts": [
    {
      "email": "ihr_name@web.de",
      "password": "ihr_anwendungspasswort",
      "imap_server": "imap.web.de",
      "imap_port": 993
    }
  ],
  "backup_dir": "archive",
  "max_retries": 5,
  "retry_delay_seconds": 10
}
```

> [!WARNING]
> Schützen Sie die Datei `config.json`, da sie Passwörter im Klartext enthält:
> `chmod 600 config.json`

### 2. Skript ausführen

Sie können das Skript direkt über das Terminal ausführen:

```bash
./backup_mails.py
```

#### CLI-Optionen:
- `-c` / `--config`: Anderer Pfad zur Konfigurationsdatei (Standard: `config.json`).
- `-l` / `--log`: Anderer Pfad für die Log-Datei (Standard: `backup.log`).
- `-d` / `--dry-run`: Führt nur eine Simulation durch (schaut nach neuen Mails, lädt sie aber nicht herunter).
- `-v` / `--verbose`: Zeigt detaillierte Debug-Informationen auf der Konsole an.

Beispiel für Simulation:
```bash
./backup_mails.py --dry-run
```

---

## Verzeichnisstruktur nach dem Backup

Wenn das Skript ausgeführt wird, wird folgende Struktur im Backup-Verzeichnis (Standard: `archive/`) erstellt:

```text
archive/
└── ihr_name_web.de/
    ├── INBOX/
    │   ├── .sync_metadata.json   <-- Speichert UIDs und UIDVALIDITY
    │   ├── 1.eml                 <-- E-Mail 1 im Standard MIME-Format
    │   ├── 2.eml
    │   └── ...
    ├── Gesendet/
    │   ├── .sync_metadata.json
    │   ├── 5.eml
    │   └── ...
    └── ...
```

Die heruntergeladenen `.eml`-Dateien können mit Standard-E-Mail-Programmen (wie Mozilla Thunderbird, Apple Mail oder Microsoft Outlook) per Doppelklick direkt geöffnet werden.
