# Backup Criptografado — CondoReservas

Backups do banco de dados são criptografados com **Fernet** (AES-128-CBC + HMAC) usando a mesma `ENCRYPTION_KEY` do `.env`.

---

## Estrutura

```
backups/
  condoreservas_20260514_230000.db.enc   # SQLite
  condoreservas_20260514_230000.sql.enc  # PostgreSQL
logs/
  app.log                         # Registro de cada backup/restauração
```

---

## Executar backup manualmente

```bash
python backup_encrypted.py
```

Saída esperada:
```
✅ Backup criptografado: backups/condoreservas_20260514_230000.sql.enc
```

---

## Agendar backup automático (Windows — Task Scheduler)

Roda todo dia às 23:00, fora do expediente, com o mesmo usuário que executa o Flask:

```bat
schtasks /create /tn "CondoReservasBackup" ^
  /tr "python C:\Dev\condoreservas\backup_encrypted.py" ^
  /sc daily /st 23:00 /f
```

Para verificar se a tarefa foi criada:
```bat
schtasks /query /tn "CondoReservasBackup"
```

Para remover:
```bat
schtasks /delete /tn "CondoReservasBackup" /f
```

---

## Restaurar um backup

```bash
python restore_encrypted.py backups/condoreservas_20260514_230000.sql.enc
```

- SQLite (`.db.enc`): substitui `condoreservas.db` (o anterior é salvo como `condoreservas.db.bak`)
- PostgreSQL (`.sql.enc`): executa `psql` para restaurar o dump

A mesma `ENCRYPTION_KEY` do `.env` que foi usada no backup é obrigatória.

---

## Retenção

Os backups **não são apagados automaticamente**. Para limpar arquivos antigos (exemplo: manter só os últimos 30 dias):

```powershell
# PowerShell — apaga .enc com mais de 30 dias
Get-ChildItem backups\*.enc | Where-Object { $_.LastWriteTime -lt (Get-Date).AddDays(-30) } | Remove-Item
```

---

## Requisitos

| Dependência | Versão |
|---|---|
| cryptography | >= 41.0.0 |
| psycopg2-binary | >= 2.9.0 |
| python-dotenv | >= 1.0.0 |

Para PostgreSQL: `pg_dump` e `psql` devem estar no PATH (instalados com PostgreSQL client tools).

---

## Segurança

- Nunca commitar arquivos `.enc` no git (já ignorados via `.gitignore`)
- Nunca commitar o `.env` (já ignorado)
- Guardar a `ENCRYPTION_KEY` em local seguro separado — sem ela, os backups não podem ser restaurados
