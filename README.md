# IMAP Email Backup Client

A robust Python utility to incrementally backup emails from multiple IMAP accounts to a local archive directory.

## Features

- **Multi-Account Support**: Backup emails from multiple different email addresses and providers simultaneously.
- **Incremental Backup**: Tracks IMAP UIDs and `UIDVALIDITY` to download only new emails. Already backed-up emails are skipped, saving bandwidth and time.
- **Change Detection**: If the folder `UIDVALIDITY` changes on the server (e.g. due to server migrations or folder resets), the old backup directory is safely archived and a fresh sync is started to prevent ID conflicts.
- **Clean Filenames**: Saves emails in standard MIME format (`.eml`) using the pattern `YYYY-MM-DD_HH-MM-SS_Subject_uid_UID.eml`.
- **Automatic Filename Migration**: Older file naming conventions are automatically migrated to the new clean format upon the first run.
- **Robust Connection Handling**: Auto-reconnect and retry logic with exponential backoff if network or protocol issues arise.
- **Zero External Dependencies**: Relies entirely on the Python Standard Library.
- **Simulation Mode**: Run a simulation using `--dry-run` to see what would be downloaded without actually writing email files.

## Prerequisites

- **Python 3.6+**
- An **email account** with IMAP access enabled.
  > [!IMPORTANT]
  > Most email providers (like Gmail, Outlook, Web.de, GMX) require you to manually enable IMAP access in your account settings.
  > In addition, it is highly recommended (and often mandatory) to generate an **App-Specific Password** (or Application Password) in your email provider's security settings and use it in the configuration instead of your master password.

## Installation & Setup

1. Clone or download this repository.
2. Copy the configuration template `config.json.template` to `config.json`:
   ```bash
   cp config.json.template config.json
   ```
3. Edit `config.json` and enter your account credentials and settings:
   ```json
   {
     "accounts": [
       {
         "email": "your_email_1@example.com",
         "password": "your_app_specific_password_1",
         "imap_server": "imap.example.com",
         "imap_port": 993
       }
     ],
     "backup_dir": "archive",
     "max_retries": 5,
     "retry_delay_seconds": 10
   }
   ```

## Usage

Simply run the script using Python:

```bash
python backup_mails.py
```

### Command Line Options

- `-c`, `--config`: Path to the JSON configuration file (default: `config.json`).
- `-l`, `--log`: Path to the log file (default: `backup.log`). Detailed logs are always written to this file.
- `-d`, `--dry-run`: Runs a simulation (lists the emails that would be downloaded without downloading them).
- `-v`, `--verbose`: Enables verbose debug output on the console.

Example run with simulation and detailed console logs:
```bash
python backup_mails.py --dry-run --verbose
```

## Project Structure

- [backup_mails.py](file:///Users/robinwerner/MailStore/backup_mails.py): The main script containing the backup logic.
- `config.json`: The active configuration file containing credentials (ignored by Git).
- [config.json.template](file:///Users/robinwerner/MailStore/config.json.template): Sample configuration template.
- `.gitignore`: Excludes sensitive data, logs, and local backups (`archive/`) from being committed.
