"""
Backup off-site do banco de produção via e-mail.

Variáveis necessárias no .env:
  DATABASE_URL_PRODUCAO  (ou DATABASE_URL como fallback)
  EMAIL_USER             ex: seuemail@gmail.com
  EMAIL_PASS             App Password do Gmail (16 chars)

Uso:
  python backup_producao.py
"""
import os
import sys
import subprocess
import smtplib
import datetime
from email.message import EmailMessage

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    print("[AVISO] python-dotenv não instalado — lendo variáveis do ambiente do sistema.")

DESTINO   = os.getenv("BACKUP_EMAIL_DESTINO")
DB_URL    = os.getenv("DATABASE_URL_PRODUCAO") or os.getenv("DATABASE_URL")
EMAIL_USR = os.getenv("EMAIL_USER")
EMAIL_PWD = os.getenv("EMAIL_PASS")

if not DB_URL:
    print("[ERRO] DATABASE_URL_PRODUCAO / DATABASE_URL não definido.")
    sys.exit(1)
if not EMAIL_USR or not EMAIL_PWD or not DESTINO:
    print("[ERRO] EMAIL_USER, EMAIL_PASS e BACKUP_EMAIL_DESTINO precisam estar definidos no .env.")
    sys.exit(1)

stamp     = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
dump_file = f"backup_condoreservas_{stamp}.dump"

print(f"[1/3] Gerando dump → {dump_file}")
ret = subprocess.run(["pg_dump", DB_URL, "-F", "c", "-f", dump_file]).returncode
if ret != 0 or not os.path.exists(dump_file):
    print(f"[ERRO] pg_dump falhou (código {ret}). Verifique se pg_dump está no PATH.")
    sys.exit(1)

size_kb = os.path.getsize(dump_file) // 1024
print(f"[1/3] Dump gerado com sucesso — {size_kb} KB")

print(f"[2/3] Enviando e-mail para {DESTINO} via {EMAIL_USR}...")
msg = EmailMessage()
msg["Subject"] = f"Backup CondoReservas — {stamp}"
msg["From"]    = EMAIL_USR
msg["To"]      = DESTINO
msg.set_content(
    f"Backup automático do banco de produção.\n"
    f"Data/hora: {stamp}\n"
    f"Tamanho: {size_kb} KB\n\n"
    "Para restaurar:\n"
    "  pg_restore -d <DATABASE_URL> -F c backup_condoreservas_<stamp>.dump"
)

with open(dump_file, "rb") as f:
    msg.add_attachment(
        f.read(),
        maintype="application",
        subtype="octet-stream",
        filename=dump_file,
    )

try:
    with smtplib.SMTP("smtp.gmail.com", 587) as smtp:
        smtp.ehlo()
        smtp.starttls()
        smtp.login(EMAIL_USR, EMAIL_PWD)
        smtp.send_message(msg)
    print("[2/3] E-mail enviado com sucesso.")
except Exception as e:
    print(f"[ERRO] Falha no envio do e-mail: {e}")
    os.remove(dump_file)
    sys.exit(1)

print("[3/3] Removendo dump local...")
os.remove(dump_file)
print("[3/3] Dump local removido.")
print(f"\n[OK] Backup concluído — {dump_file} enviado para {DESTINO}.")
