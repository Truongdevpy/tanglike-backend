import os
import sys
import gzip
import shutil
import sqlite3
import argparse
from datetime import datetime, timedelta
from pathlib import Path

def backup_sqlite(source_path: str, backup_dir: Path, retention_days: int = 7) -> str:
    """
    Creates an atomic, consistent SQLite snapshot using the SQLite Online Backup API.
    Compresses with gzip and rotates old snapshots.
    """
    backup_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    raw_backup_path = backup_dir / f"tanglike_backup_{timestamp}.db"
    gz_backup_path = backup_dir / f"tanglike_backup_{timestamp}.db.gz"

    print(f"[*] Initiating SQLite online backup from: {source_path}")
    source_conn = sqlite3.connect(source_path)
    dest_conn = sqlite3.connect(str(raw_backup_path))

    try:
        source_conn.backup(dest_conn)
        dest_conn.close()
        source_conn.close()
        print(f"[*] Snapshot created at {raw_backup_path}. Compressing with gzip...")

        # Compress to .db.gz
        with open(raw_backup_path, "rb") as f_in:
            with gzip.open(gz_backup_path, "wb") as f_out:
                shutil.copyfileobj(f_in, f_out)
        
        # Remove raw uncompressed file
        os.remove(raw_backup_path)
        print(f"[+] Backup completed successfully: {gz_backup_path} ({gz_backup_path.stat().st_size:,} bytes)")
    except Exception as e:
        if raw_backup_path.exists():
            os.remove(raw_backup_path)
        raise RuntimeError(f"Backup failed: {e}")

    # Retention clean up
    cutoff = datetime.utcnow() - timedelta(days=retention_days)
    print(f"[*] Cleaning up backups older than {retention_days} days (before {cutoff.isoformat()})...")
    cleaned_count = 0
    for f in backup_dir.glob("tanglike_backup_*.db.gz"):
        file_mtime = datetime.utcfromtimestamp(f.stat().st_mtime)
        if file_mtime < cutoff:
            f.unlink()
            cleaned_count += 1
            print(f"[-] Removed expired backup: {f.name}")

    print(f"[+] Retention check finished. {cleaned_count} expired backups removed.")
    return str(gz_backup_path)

def verify_backup(backup_gz_path: str) -> bool:
    """Verifies that the compressed backup can be unpacked and queried."""
    temp_verify_path = Path(backup_gz_path).with_suffix(".tmp_verify")
    try:
        with gzip.open(backup_gz_path, "rb") as f_in:
            with open(temp_verify_path, "wb") as f_out:
                shutil.copyfileobj(f_in, f_out)

        conn = sqlite3.connect(str(temp_verify_path))
        c = conn.cursor()
        users_count = c.execute("SELECT count(*) FROM users").fetchone()[0]
        services_count = c.execute("SELECT count(*) FROM services").fetchone()[0]
        conn.close()
        temp_verify_path.unlink()
        print(f"[OK] Verification SUCCESS: Backup is valid and readable (Users: {users_count}, Services: {services_count})")
        return True
    except Exception as e:
        if temp_verify_path.exists():
            temp_verify_path.unlink()
        print(f"[FAIL] Verification FAILED: {e}")
        return False

def main():
    parser = argparse.ArgumentParser(description="TangLike Database Backup and Rotation Utility")
    parser.add_argument("--db-path", default="tanglike.db", help="Path to sqlite db")
    parser.add_argument("--backup-dir", default="backups", help="Target directory for backups")
    parser.add_argument("--retention-days", type=int, default=7, help="Days to retain snapshots")
    parser.add_argument("--verify", action="store_true", default=True, help="Verify backup integrity after creation")
    args = parser.parse_args()

    source = Path(args.db_path)
    if not source.exists():
        print(f"[!] Source database not found: {source}")
        sys.exit(1)

    backup_dir = Path(args.backup_dir)
    gz_path = backup_sqlite(str(source), backup_dir, args.retention_days)

    if args.verify:
        is_ok = verify_backup(gz_path)
        if not is_ok:
            sys.exit(1)

if __name__ == "__main__":
    main()
