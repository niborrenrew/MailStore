#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Web.de E-Mail Backup Script
A robust Python utility to incrementally backup emails from multiple Web.de accounts 
to a local archive directory using IMAP.
"""

import os
import sys
import json
import time
import socket
import ssl
import re
import base64
import logging
import argparse
import datetime
import imaplib
import email
from email.header import decode_header
from email.utils import parsedate_to_datetime
from typing import Dict, List, Tuple, Optional, Any, Set

# Set default socket timeout (in seconds) to prevent hanging connections
socket.setdefaulttimeout(30)


def decode_imap_utf7(s: str) -> str:
    """
    Decodes an IMAP mailbox name encoded in Modified UTF-7 (RFC 3501, section 5.1.3).
    Example: "INBOX.Ges&AMQ-ndet" -> "INBOX/Gesendet"
    """
    if isinstance(s, bytes):
        s = s.decode('ascii')
    
    parts = []
    i = 0
    n = len(s)
    while i < n:
        c = s[i]
        if c == '&':
            # Look for the terminating '-'
            j = s.find('-', i + 1)
            if j == -1:
                # Malformed UTF-7 block, keep remaining string as is
                parts.append(s[i:])
                break
            
            block = s[i+1:j]
            if not block:
                # '&-' represents '&'
                parts.append('&')
            else:
                # Replace ',' with '/' for standard base64 decoding
                b64_str = block.replace(',', '/')
                # Add base64 padding if needed
                pad = len(b64_str) % 4
                if pad:
                    b64_str += '=' * (4 - pad)
                try:
                    decoded_bytes = base64.b64decode(b64_str)
                    parts.append(decoded_bytes.decode('utf-16-be'))
                except Exception:
                    # Fallback to the original block if decoding fails
                    parts.append(s[i:j+1])
            i = j + 1
        else:
            parts.append(c)
            i += 1
            
    return "".join(parts)


def sanitize_folder_name(name: str) -> str:
    """
    Sanitizes folder name parts to be safe for directory creation on Windows, macOS, Linux.
    """
    # Replace illegal characters
    illegal_chars = ['<', '>', ':', '"', '\\', '|', '?', '*']
    for char in illegal_chars:
        name = name.replace(char, '_')
    # Strip leading/trailing spaces and dots which can cause directory traversal issues or OS errors
    name = name.strip(' .')
    if not name:
        name = "unnamed_folder"
    return name


def get_local_folder_path(base_dir: str, imap_folder_name: str, delimiter: str = '/') -> str:
    """
    Translates an IMAP folder name to a sanitized local directory path.
    """
    decoded = decode_imap_utf7(imap_folder_name)
    parts = decoded.split(delimiter)
    sanitized_parts = [sanitize_folder_name(p) for p in parts if p]
    return os.path.join(base_dir, *sanitized_parts)


def parse_list_response(line: bytes) -> Optional[Tuple[List[str], str, str]]:
    """
    Parses a single line of IMAP LIST command response.
    Returns: Tuple of (Flags, Delimiter, Folder Name) or None if parsing fails.
    
    Example input: b'(\\HasNoChildren \\Drafts) "/" "Drafts"'
    """
    s = line.decode('latin1')
    # Match the flags in parentheses
    flags_match = re.search(r'\((.*?)\)', s)
    if not flags_match:
        return None
    
    flags = flags_match.group(1).split()
    rest = s[flags_match.end():].strip()
    
    # Parse the remainder: delimiter and folder name
    parts = []
    in_quote = False
    current = []
    escaped = False
    
    for char in rest:
        if escaped:
            current.append(char)
            escaped = False
        elif char == '\\':
            escaped = True
        elif char == '"':
            in_quote = not in_quote
        elif char.isspace() and not in_quote:
            if current:
                parts.append("".join(current))
                current = []
        else:
            current.append(char)
    if current:
        parts.append("".join(current))
        
    if len(parts) >= 2:
        delimiter = parts[0]
        if delimiter.upper() == 'NIL':
            delimiter = '/'
        folder_name = " ".join(parts[1:])
        return flags, delimiter, folder_name
    elif len(parts) == 1:
        return flags, '/', parts[0]
    return None


def quote_mailbox(name: str) -> str:
    """
    Quotes a mailbox name if it is not already quoted.
    This is required for mailbox names containing spaces or special characters.
    """
    if name.startswith('"') and name.endswith('"'):
        return name
    return '"' + name.replace('\\', '\\\\').replace('"', '\\"') + '"'


def decode_mime_header(header_value: str) -> str:
    """
    Decodes MIME encoded headers like '=?UTF-8?B?...?=' to plain unicode.
    """
    if not header_value:
        return ""
    try:
        decoded_parts = decode_header(header_value)
        result_parts = []
        for text, charset in decoded_parts:
            if isinstance(text, bytes):
                if charset:
                    try:
                        result_parts.append(text.decode(charset))
                    except Exception:
                        result_parts.append(text.decode('utf-8', errors='ignore'))
                else:
                    result_parts.append(text.decode('utf-8', errors='ignore'))
            else:
                result_parts.append(text)
        return "".join(result_parts)
    except Exception:
        return str(header_value)


def clean_filename_part(s: str) -> str:
    """
    Cleans a string to be suitable as a filename part.
    Replaces spaces, slashes and special characters.
    """
    # Replace line breaks, carriage returns, tabs
    s = s.replace('\n', '').replace('\r', '').replace('\t', '')
    # Replace spaces and common path delimiters with underscores
    s = s.replace(' ', '_').replace('/', '_').replace('\\', '_')
    # Keep only alphanumeric characters, underscores, hyphens, and dots
    s = re.sub(r'[^a-zA-Z0-9_\-\.]', '', s)
    # Remove consecutive underscores
    s = re.sub(r'_+', '_', s)
    return s.strip('_')


def migrate_existing_filenames(local_path: str, logger: logging.Logger):
    """
    Identifies files named under older patterns and renames them to the new clean format:
    'YYYY-MM-DD_HH-MM-SS_Subject_uid_UID.eml'.
    """
    if not os.path.isdir(local_path):
        return
        
    for filename in os.listdir(local_path):
        # Skip if already in the new format
        if re.match(r'^\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}_.*_uid_\d+\.eml$', filename):
            continue
            
        # Match old formats: '{uid}.eml' or '{uid}_{date}_{sender}_{subject}.eml'
        match = re.match(r'^(\d+)(?:\.eml|_\d{8}_\d{6}_.*\.eml)$', filename)
        if match:
            uid = match.group(1)
            file_path = os.path.join(local_path, filename)
            try:
                with open(file_path, 'rb') as f:
                    raw_email = f.read()
                
                # Parse mail headers
                msg = email.message_from_bytes(raw_email)
                
                # Extract headers
                date_str = "unknown"
                if 'date' in msg:
                    try:
                        dt = parsedate_to_datetime(msg['date'])
                        date_str = dt.strftime("%Y-%m-%d_%H-%M-%S")
                    except Exception:
                        pass
                        
                subj_str = "no_subject"
                if 'subject' in msg:
                    subj_str = clean_filename_part(decode_mime_header(msg['subject']))
                
                # Build new filename and path
                new_filename = f"{date_str}_{subj_str[:120]}_uid_{uid}.eml"
                new_file_path = os.path.join(local_path, new_filename)
                
                # Rename the file if new file doesn't exist
                if not os.path.exists(new_file_path):
                    os.rename(file_path, new_file_path)
                    logger.info(f"Datei umbenannt (Migration): {filename} -> {new_filename}")
                else:
                    # If it already exists, just delete the old one to avoid duplicates
                    os.remove(file_path)
            except Exception as e:
                logger.error(f"Fehler bei Migration von {filename}: {e}")


def connect_and_login(account: Dict[str, Any], max_retries: int, delay_seconds: int, logger: logging.Logger) -> Optional[imaplib.IMAP4_SSL]:
    """
    Establishes an SSL connection to the IMAP server and logs in.
    Supports retry logic with exponential backoff.
    """
    email_addr = account["email"]
    password = account["password"]
    server = account.get("imap_server", "imap.web.de")
    port = account.get("imap_port", 993)
    
    retries = 0
    current_delay = delay_seconds
    
    while retries < max_retries:
        try:
            logger.info(f"Verbinde zu {server}:{port} für {email_addr} (Versuch {retries + 1}/{max_retries})...")
            
            # Set up secure SSL context
            context = ssl.create_default_context()
            imap = imaplib.IMAP4_SSL(server, port, ssl_context=context)
            
            logger.info(f"Logge ein als {email_addr}...")
            imap.login(email_addr, password)
            logger.info(f"Erfolgreich eingeloggt als {email_addr}.")
            return imap
            
        except imaplib.IMAP4.error as e:
            err_msg = str(e).lower()
            # If it looks like credentials error, fail immediately to prevent account lockouts
            if any(term in err_msg for term in ["auth", "login", "credential", "fail", "passwort", "benutzername"]):
                logger.error(f"Authentifizierungsfehler für {email_addr}. Bitte Anmeldedaten / Web.de-App-Passwort prüfen. Details: {e}")
                return None
            logger.warning(f"IMAP-Fehler beim Verbindungsaufbau für {email_addr}: {e}. Erneuter Versuch in {current_delay}s...")
        except (socket.timeout, socket.error, ssl.SSLError) as e:
            logger.warning(f"Netzwerkfehler beim Verbindungsaufbau für {email_addr}: {e}. Erneuter Versuch in {current_delay}s...")
        except Exception as e:
            logger.warning(f"Unerwarteter Fehler beim Verbindungsaufbau für {email_addr}: {e}. Erneuter Versuch in {current_delay}s...")
            
        retries += 1
        if retries < max_retries:
            time.sleep(current_delay)
            current_delay *= 2
            
    logger.error(f"Konnte nach {max_retries} Versuchen keine Verbindung zu {email_addr} herstellen.")
    return None


def reconnect_imap(account: Dict[str, Any], old_imap: Optional[imaplib.IMAP4_SSL], max_retries: int, delay_seconds: int, logger: logging.Logger) -> Optional[imaplib.IMAP4_SSL]:
    """
    Safely logs out of the old IMAP connection and establishes a fresh one.
    """
    if old_imap:
        logger.info("Schließe alte IMAP-Verbindung...")
        try:
            old_imap.logout()
        except Exception:
            pass
    
    # Wait a brief moment to avoid flooding the server
    time.sleep(2)
    return connect_and_login(account, max_retries, delay_seconds, logger)


def sync_folder(imap: imaplib.IMAP4_SSL, account_dir: str, imap_folder_name: str, delimiter: str, dry_run: bool, logger: logging.Logger) -> bool:
    """
    Synchronizes a single IMAP folder. Downloads only new emails based on UID and UIDVALIDITY.
    """
    local_path = get_local_folder_path(account_dir, imap_folder_name, delimiter)
    
    # Select folder in Read-Only mode to avoid any modifications on the server
    try:
        quoted_folder_name = quote_mailbox(imap_folder_name)
        status, data = imap.select(quoted_folder_name, readonly=True)
        if status != 'OK':
            logger.error(f"Konnte Ordner '{imap_folder_name}' nicht auswählen: {status}")
            return False
    except Exception as e:
        logger.error(f"Ausnahmefehler bei Auswahl von Ordner '{imap_folder_name}': {e}")
        return False
        
    # Get UIDVALIDITY
    uidvalidity = None
    if 'UIDVALIDITY' in imap.untagged_responses:
        uidvalidity = imap.untagged_responses['UIDVALIDITY'][0].decode('ascii')
        
    if not uidvalidity:
        logger.warning(f"UIDVALIDITY für '{imap_folder_name}' konnte nicht ermittelt werden. Verwende 'unknown'.")
        uidvalidity = 'unknown'
        
    metadata_file = os.path.join(local_path, ".sync_metadata.json")
    metadata = {
        "uidvalidity": uidvalidity,
        "downloaded_uids": []
    }
    
    # Handle existing directory and check UIDVALIDITY consistency
    if os.path.exists(local_path):
        if os.path.exists(metadata_file):
            try:
                with open(metadata_file, 'r', encoding='utf-8') as f:
                    stored_metadata = json.load(f)
                
                if stored_metadata.get("uidvalidity") == uidvalidity:
                    metadata = stored_metadata
                else:
                    # UIDVALIDITY changed. Archive existing backup to prevent ID mismatch or corruption.
                    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
                    archive_path = f"{local_path}_invalid_uidvalidity_{timestamp}"
                    logger.warning(
                        f"UIDVALIDITY hat sich für '{imap_folder_name}' geändert "
                        f"(gespeichert: {stored_metadata.get('uidvalidity')}, Server: {uidvalidity}). "
                        f"Archiviere alten Backup-Ordner nach: {archive_path}"
                    )
                    if not dry_run:
                        os.rename(local_path, archive_path)
                        os.makedirs(local_path, exist_ok=True)
            except Exception as e:
                logger.error(f"Fehler beim Lesen der Metadaten für '{imap_folder_name}': {e}. Synchronisiere neu.")
        else:
            logger.warning(f"Ordner für '{imap_folder_name}' existiert, aber Metadaten fehlen. Starte vollständigen Sync.")
    else:
        if not dry_run:
            os.makedirs(local_path, exist_ok=True)
            
    # Run migration of old {uid}.eml files to the new format if any exist
    if not dry_run:
        migrate_existing_filenames(local_path, logger)
            
    # Search for all mail UIDs in folder
    try:
        status, search_data = imap.uid('search', None, 'ALL')
        if status != 'OK':
            logger.error(f"Suche in '{imap_folder_name}' fehlgeschlagen: {status}")
            return False
    except Exception as e:
        logger.error(f"Fehler bei Suche in '{imap_folder_name}': {e}")
        return False
        
    uids = []
    if search_data and search_data[0]:
        uids = [uid.decode('ascii') for uid in search_data[0].split()]
        
    # Check which UIDs are missing locally
    downloaded_set = set(metadata.get("downloaded_uids", []))
    to_download = [uid for uid in uids if uid not in downloaded_set]
    
    if not to_download:
        logger.info(f"Ordner '{imap_folder_name}' ist aktuell. ({len(uids)} E-Mails)")
        return True
        
    logger.info(f"Synchronisiere Ordner '{imap_folder_name}': {len(to_download)} neue E-Mails von {len(uids)} insgesamt.")
    
    if dry_run:
        logger.info(f"[DRY-RUN] Würde {len(to_download)} E-Mails herunterladen.")
        return True
        
    success_count = 0
    skipped_count = 0
    
    for idx, uid in enumerate(to_download, 1):
        try:
            # Fetch raw email content using RFC822
            status, fetch_data = imap.uid('fetch', uid, '(RFC822)')
            if status != 'OK':
                logger.error(f"[{idx}/{len(to_download)}] E-Mail UID {uid} konnte nicht abgerufen werden: {status}")
                skipped_count += 1
                continue
                
            raw_email = None
            for part in fetch_data:
                if isinstance(part, tuple):
                    raw_email = part[1]
                    break
                    
            if not raw_email:
                logger.error(f"[{idx}/{len(to_download)}] E-Mail UID {uid} lieferte keine Daten.")
                skipped_count += 1
                continue
                
            # Extract headers for a descriptive filename
            try:
                msg = email.message_from_bytes(raw_email)
                
                date_str = "unknown"
                if 'date' in msg:
                    try:
                        dt = parsedate_to_datetime(msg['date'])
                        date_str = dt.strftime("%Y-%m-%d_%H-%M-%S")
                    except Exception:
                        pass
                        
                subj_str = "no_subject"
                if 'subject' in msg:
                    subj_str = clean_filename_part(decode_mime_header(msg['subject']))
            except Exception as e:
                logger.warning(f"Konnte E-Mail-Header für UID {uid} nicht parsen ({e}). Verwende Standardnamen.")
                date_str = "unknown"
                subj_str = "no_subject"

            # Construct descriptive filename: YYYY-MM-DD_HH-MM-SS_Subject_uid_UID.eml
            new_filename = f"{date_str}_{subj_str[:120]}_uid_{uid}.eml"
            file_path = os.path.join(local_path, new_filename)
            temp_file_path = os.path.join(local_path, f"temp_{uid}.eml")
            
            with open(temp_file_path, 'wb') as f:
                f.write(raw_email)
                
            os.replace(temp_file_path, file_path)
            
            # Record downloaded UID
            metadata["downloaded_uids"].append(uid)
            success_count += 1
            
            # Periodically save metadata to disk (every 10 emails) to avoid data loss on crash
            if success_count % 10 == 0:
                with open(metadata_file, 'w', encoding='utf-8') as f:
                    json.dump(metadata, f, indent=2)
                    
            if idx % 50 == 0 or idx == len(to_download):
                logger.info(f"[{idx}/{len(to_download)}] {success_count} E-Mails heruntergeladen...")
                
        except (imaplib.IMAP4.abort, imaplib.IMAP4.error, socket.error, ssl.SSLError) as e:
            # Network or IMAP protocol error -> Raise it to trigger a reconnect and retry in the outer loop
            logger.error(f"[{idx}/{len(to_download)}] IMAP/Netzwerkfehler bei UID {uid}: {e}. Breche Ordner-Sync ab für Reconnect...")
            raise
        except OSError as e:
            # Local disk/IO error (e.g. SMB disconnect, disk full) -> Raise to abort sync and prevent marking as downloaded
            logger.error(f"[{idx}/{len(to_download)}] Festplatten-/Netzwerkschreibfehler bei UID {uid}: {e}. Breche Backup ab...")
            raise
        except Exception as e:
            # Local/Processing error for this specific email (e.g. parsing error) -> Log and skip to avoid infinite loops
            logger.error(f"[{idx}/{len(to_download)}] Lokaler Verarbeitungsfehler bei UID {uid} (wird übersprungen): {e}")
            skipped_count += 1
            # Mark it as downloaded so we don't get stuck on this corrupt mail forever
            metadata["downloaded_uids"].append(uid)
            
    # Final metadata save
    try:
        with open(metadata_file, 'w', encoding='utf-8') as f:
            json.dump(metadata, f, indent=2)
    except Exception as e:
        logger.error(f"Konnte Metadaten-Datei für '{imap_folder_name}' nicht schreiben: {e}")
        
    logger.info(f"Fertig mit '{imap_folder_name}': {success_count} heruntergeladen, {skipped_count} übersprungen/fehlgeschlagen.")
    return True


def backup_account(account: Dict[str, Any], base_backup_dir: str, max_retries: int, delay_seconds: int, dry_run: bool, logger: logging.Logger) -> bool:
    """
    Performs the complete backup sequence for a single account.
    """
    email_addr = account["email"]
    logger.info(f"=== Starte Backup für Account: {email_addr} ===")
    
    # Separate archive directory for each email address
    account_dir = os.path.join(base_backup_dir, sanitize_folder_name(email_addr))
    if not dry_run:
        os.makedirs(account_dir, exist_ok=True)
        
    imap = connect_and_login(account, max_retries, delay_seconds, logger)
    if not imap:
        logger.error(f"Konnte keine Verbindung für {email_addr} herstellen. Account wird übersprungen.")
        return False
        
    success = True
    try:
        # Get list of all folders
        logger.info("Rufe Ordnerliste vom Server ab...")
        status, list_data = imap.list()
        if status != 'OK':
            logger.error(f"Ordnerliste konnte nicht abgerufen werden: {status}")
            return False
            
        folders = []
        for line in list_data:
            if not line:
                continue
            parsed = parse_list_response(line)
            if parsed:
                flags, delimiter, folder_name = parsed
                # Skip folders that have the '\Noselect' flag (e.g. system parents)
                has_noselect = any(f.lower() == '\\noselect' for f in flags)
                if has_noselect:
                    logger.debug(f"Überspringe Noselect-Ordner: {folder_name}")
                    continue
                folders.append((folder_name, delimiter))
                
        logger.info(f"{len(folders)} Ordner zum Synchronisieren gefunden.")
        
        # Sync each folder with retry mechanisms on disconnect
        for folder_name, delimiter in folders:
            max_folder_attempts = 10
            attempt = 0
            folder_success = False
            
            while attempt < max_folder_attempts:
                try:
                    folder_success = sync_folder(imap, account_dir, folder_name, delimiter, dry_run, logger)
                    if folder_success:
                        break
                    else:
                        # If sync_folder returned False without raising (e.g. SELECT command failed), reconnect and retry
                        attempt += 1
                        logger.warning(f"Sync für Ordner '{folder_name}' nicht erfolgreich. Reconnect und Wiederholung ({attempt}/{max_folder_attempts})...")
                        imap = reconnect_imap(account, imap, max_retries, delay_seconds, logger)
                        if not imap:
                            break
                except (imaplib.IMAP4.abort, imaplib.IMAP4.error, socket.error, ssl.SSLError) as e:
                    attempt += 1
                    logger.warning(f"Verbindung verloren bei Ordner '{folder_name}' ({e}). Reconnect und Wiederholung ({attempt}/{max_folder_attempts})...")
                    imap = reconnect_imap(account, imap, max_retries, delay_seconds, logger)
                    if not imap:
                        break
                except Exception as e:
                    logger.error(f"Kritischer Fehler bei Synchronisation von '{folder_name}': {e}")
                    break
            
            if not folder_success:
                logger.error(f"Ordner '{folder_name}' konnte nach {max_folder_attempts} Versuchen nicht vollständig synchronisiert werden.")
                success = False
                
    except Exception as e:
        logger.error(f"Fehler im Backup-Prozess für {email_addr}: {e}")
        success = False
    finally:
        # Gracefully log out
        logger.info(f"Melde Client für {email_addr} ab...")
        try:
            imap.logout()
        except Exception:
            pass
            
    logger.info(f"=== Backup-Prozess für {email_addr} beendet (Erfolgreich: {success}) ===\n")
    return success


def main():
    parser = argparse.ArgumentParser(description="Inkrementeller E-Mail-Backup-Client für Web.de Accounts.")
    parser.add_argument("-c", "--config", default="config.json", help="Pfad zur JSON-Konfigurationsdatei.")
    parser.add_argument("-l", "--log", default="backup.log", help="Pfad zur Log-Datei.")
    parser.add_argument("-d", "--dry-run", action="store_true", help="Führt eine Simulation durch, ohne E-Mails herunterzuladen.")
    parser.add_argument("-v", "--verbose", action="store_true", help="Zeigt detaillierte Debug-Informationen an.")
    args = parser.parse_args()
    
    # Configure logging
    log_level = logging.DEBUG if args.verbose else logging.INFO
    logger = logging.getLogger("MailBackup")
    logger.setLevel(logging.DEBUG)  # Keep internal level at debug
    
    # Console handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(log_level)
    console_format = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s', '%H:%M:%S')
    console_handler.setFormatter(console_format)
    logger.addHandler(console_handler)
    
    # File handler
    try:
        file_handler = logging.FileHandler(args.log, encoding='utf-8')
        file_handler.setLevel(logging.DEBUG)  # Always log details to file
        file_format = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
        file_handler.setFormatter(file_format)
        logger.addHandler(file_handler)
    except Exception as e:
        print(f"WARNUNG: Konnte Log-Datei '{args.log}' nicht erstellen: {e}", file=sys.stderr)
        
    logger.info("Starte Web.de Mail Backup Client...")
    
    # Load configuration
    if not os.path.exists(args.config):
        logger.error(
            f"Konfigurationsdatei '{args.config}' nicht gefunden.\n"
            f"Bitte erstelle eine Datei basierend auf 'config.json.template'."
        )
        sys.exit(1)
        
    try:
        with open(args.config, 'r', encoding='utf-8') as f:
            config = json.load(f)
    except json.JSONDecodeError as e:
        logger.error(f"Fehler beim Parsen der JSON-Konfiguration '{args.config}': {e}")
        sys.exit(1)
    except Exception as e:
        logger.error(f"Konnte Konfigurationsdatei '{args.config}' nicht lesen: {e}")
        sys.exit(1)
        
    # Read backup params from config
    accounts = config.get("accounts", [])
    backup_dir = config.get("backup_dir", "archive")
    max_retries = config.get("max_retries", 5)
    retry_delay_seconds = config.get("retry_delay_seconds", 10)
    
    if not accounts:
        logger.error("Keine Accounts in der Konfigurationsdatei definiert.")
        sys.exit(1)
        
    logger.info(f"Es wurden {len(accounts)} Accounts geladen. Speicherverzeichnis: '{backup_dir}'.")
    if args.dry_run:
        logger.info("[DRY-RUN] Führe nur eine Simulation durch.")
        
    success_accounts = 0
    failed_accounts = []
    
    try:
        for account in accounts:
            if "email" not in account or "password" not in account:
                logger.error(f"Ungültiger Account-Eintrag in Konfiguration (E-Mail oder Passwort fehlt): {account}")
                failed_accounts.append(account.get("email", "Unbekannt"))
                continue
                
            success = backup_account(
                account=account,
                base_backup_dir=backup_dir,
                max_retries=max_retries,
                delay_seconds=retry_delay_seconds,
                dry_run=args.dry_run,
                logger=logger
            )
            if success:
                success_accounts += 1
            else:
                failed_accounts.append(account["email"])
                
    except KeyboardInterrupt:
        logger.warning("\nBackup durch Benutzer abgebrochen (KeyboardInterrupt). Beende sicher...")
        sys.exit(130)
        
    logger.info("=== Gesamter Backup-Prozess abgeschlossen ===")
    logger.info(f"Erfolgreiche Accounts: {success_accounts}/{len(accounts)}")
    if failed_accounts:
        logger.error(f"Fehlgeschlagene Accounts: {', '.join(failed_accounts)}")
        sys.exit(1)
    else:
        logger.info("Alle konfigurierten Accounts wurden erfolgreich gesichert.")
        sys.exit(0)


if __name__ == "__main__":
    main()
