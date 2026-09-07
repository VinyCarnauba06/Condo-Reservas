"""
Backup criptografado do banco de dados CondoReservas.
Suporta SQLite e PostgreSQL. Salva em backups/ com Fernet.
"""

import logging
import os
import subprocess
import sys
from datetime import datetime

from cryptography.fernet import Fernet
from dotenv import load_dotenv

load_dotenv()

LOG_DIR = "logs"
os.makedirs(LOG_DIR, exist_ok=True)

_stream = logging.StreamHandler(sys.stdout)
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(os.path.join(LOG_DIR, "app.log"), encoding="utf-8"),
        _stream,
    ],
)

BACKUP_DIR = "backups"
os.makedirs(BACKUP_DIR, exist_ok=True)


def get_fernet() -> Fernet:
    key = os.environ.get("ENCRYPTION_KEY")
    if not key:
        raise ValueError("ENCRYPTION_KEY não encontrada no .env")
    return Fernet(key.encode())


def timestamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def encrypt_file(src: str, dest: str) -> None:
    f = get_fernet()
    with open(src, "rb") as fp:
        encrypted = f.encrypt(fp.read())
    with open(dest, "wb") as fp:
        fp.write(encrypted)


def backup_sqlite(db_path: str) -> str:
    if not os.path.exists(db_path):
        raise FileNotFoundError(f"Banco SQLite não encontrado: {db_path}")
    ts = timestamp()
    enc_path = os.path.join(BACKUP_DIR, f"condoreservas_{ts}.db.enc")
    encrypt_file(db_path, enc_path)
    return enc_path


def backup_postgresql(database_url: str) -> str:
    ts = timestamp()
    tmp_sql = os.path.join(BACKUP_DIR, f"_tmp_{ts}.sql")
    enc_path = os.path.join(BACKUP_DIR, f"condoreservas_{ts}.sql.enc")
    try:
        try:
            result = subprocess.run(
                ["pg_dump", database_url, "-f", tmp_sql, "--no-password"],
                capture_output=True,
                text=True,
                timeout=120,
            )
        except FileNotFoundError:
            raise RuntimeError(
                "pg_dump não encontrado. Instale o PostgreSQL client: "
                "https://www.postgresql.org/download/"
            )
        if result.returncode != 0:
            raise RuntimeError(f"pg_dump falhou: {result.stderr.strip()}")
        encrypt_file(tmp_sql, enc_path)
    finally:
        if os.path.exists(tmp_sql):
            os.remove(tmp_sql)
    return enc_path


def run_backup() -> tuple[bool, str]:
    """Executa o backup e retorna (sucesso, mensagem). Não chama sys.exit()."""
    try:
        database_url = os.environ.get("DATABASE_URL", "")
        if not database_url:
            raise ValueError("DATABASE_URL não encontrada no .env")

        if database_url.startswith("sqlite"):
            db_path = database_url.split("///", 1)[-1]
            enc_path = backup_sqlite(db_path)
        else:
            enc_path = backup_postgresql(database_url)

        msg = f"✅ Backup criptografado: {enc_path}"
        logging.info(msg)
        return True, enc_path

    except Exception as exc:
        msg = f"Backup falhou: {exc}"
        logging.error(msg)
        return False, str(exc)


def main() -> None:
    ok, msg = run_backup()
    if not ok:
        sys.exit(1)


if __name__ == "__main__":
    main()
