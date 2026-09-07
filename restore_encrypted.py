"""
Restauração de backup criptografado do CondoReservas.
Uso: python restore_encrypted.py backups/condoreservas_20260514_140000.db.enc
"""

import os
import subprocess
import sys

from cryptography.fernet import Fernet, InvalidToken
from dotenv import load_dotenv

load_dotenv()

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


def get_fernet() -> Fernet:
    key = os.environ.get("ENCRYPTION_KEY")
    if not key:
        print("ERRO: ENCRYPTION_KEY não encontrada no .env")
        sys.exit(1)
    return Fernet(key.encode())


def decrypt_file(src: str, dest: str) -> None:
    f = get_fernet()
    try:
        with open(src, "rb") as fp:
            data = f.decrypt(fp.read())
    except InvalidToken:
        print("ERRO: ENCRYPTION_KEY incorreta ou arquivo corrompido.")
        sys.exit(1)
    with open(dest, "wb") as fp:
        fp.write(data)


def restore_sqlite(enc_path: str) -> None:
    tmp_db = enc_path + ".tmp.db"
    try:
        decrypt_file(enc_path, tmp_db)
        dest = "condoreservas.db"
        if os.path.exists(dest):
            backup_existing = dest + ".bak"
            os.replace(dest, backup_existing)
            print(f"Banco anterior salvo em: {backup_existing}")
        os.replace(tmp_db, dest)
    except Exception:
        if os.path.exists(tmp_db):
            os.remove(tmp_db)
        raise


def restore_postgresql(enc_path: str) -> None:
    database_url = os.environ.get("DATABASE_URL", "")
    if not database_url:
        print("ERRO: DATABASE_URL não encontrada no .env")
        sys.exit(1)

    tmp_sql = enc_path + ".tmp.sql"
    try:
        decrypt_file(enc_path, tmp_sql)
        try:
            result = subprocess.run(
                ["psql", database_url, "-f", tmp_sql, "--no-password"],
                capture_output=True,
                text=True,
                timeout=300,
            )
        except FileNotFoundError:
            print("ERRO: psql não encontrado. Instale o PostgreSQL client.")
            sys.exit(1)
        if result.returncode != 0:
            print(f"ERRO: psql falhou:\n{result.stderr.strip()}")
            sys.exit(1)
    finally:
        if os.path.exists(tmp_sql):
            os.remove(tmp_sql)


def main() -> None:
    if len(sys.argv) != 2:
        print("Uso: python restore_encrypted.py <arquivo.db.enc | arquivo.sql.enc>")
        sys.exit(1)

    enc_path = sys.argv[1]

    if not os.path.exists(enc_path):
        print(f"ERRO: Arquivo não encontrado: {enc_path}")
        sys.exit(1)

    if enc_path.endswith(".db.enc"):
        restore_sqlite(enc_path)
    elif enc_path.endswith(".sql.enc"):
        restore_postgresql(enc_path)
    else:
        print("ERRO: Extensão inválida. Use .db.enc (SQLite) ou .sql.enc (PostgreSQL).")
        sys.exit(1)

    print(f"✅ Restaurado de {enc_path}")


if __name__ == "__main__":
    main()
