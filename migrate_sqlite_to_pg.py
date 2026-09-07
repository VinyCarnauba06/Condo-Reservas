"""
Script de migração: SQLite → PostgreSQL (Supabase)
Exporta todos os dados do SQLite e importa no PostgreSQL preservando IDs e relações.
"""
import sqlite3
import json
import sys
import os
from decimal import Decimal
from datetime import date, datetime

# ---------------------------------------------------------------------------
# Configuração
# ---------------------------------------------------------------------------
SQLITE_PATH = os.path.join(os.path.dirname(__file__), "instance", "condoreservas.db")
PG_URL = os.environ["SUPABASE_MIGRATION_URL"]

# Colunas booleanas por tabela (SQLite armazena como 0/1)
BOOL_COLUMNS = {
    "condominios":      {"exige_adimplencia"},
    "saloes":           {"combo"},
    "reservas":         {"vistoria", "zelador", "surpresa"},
    "creditos":         {"usado"},
    "usuarios":         {"ativo"},
}

TABLES_ORDER = [
    "condominios",
    "feriados",
    "usuarios",
    "saloes",
    "bloqueios",
    "regras_regimento",
    "reservas",
    "itens_inventario",
    "historico",
    "creditos",
    "documentos_gerados",
]

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def sqlite_rows_to_dicts(conn, table):
    cur = conn.cursor()
    cur.execute(f"SELECT * FROM {table}")
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def json_default(obj):
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    if isinstance(obj, Decimal):
        return str(obj)
    raise TypeError(f"Object of type {type(obj)} is not JSON serializable")


# ---------------------------------------------------------------------------
# ETAPA 1 – Export SQLite → JSON
# ---------------------------------------------------------------------------

def export_sqlite(backup_path="sqlite_backup.json"):
    print(f"\n[EXPORT] Conectando ao SQLite: {SQLITE_PATH}")
    conn = sqlite3.connect(SQLITE_PATH)
    conn.row_factory = sqlite3.Row

    data = {}
    for table in TABLES_ORDER:
        rows = sqlite_rows_to_dicts(conn, table)
        data[table] = rows
        print(f"  {table}: {len(rows)} registros")

    conn.close()

    with open(backup_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2, default=json_default)

    print(f"[EXPORT] Backup salvo em: {backup_path}")
    return data


# ---------------------------------------------------------------------------
# ETAPA 2 – Import JSON → PostgreSQL
# ---------------------------------------------------------------------------

def pg_connect():
    import psycopg2
    from psycopg2.extras import execute_values
    conn = psycopg2.connect(PG_URL, connect_timeout=30, sslmode="require")
    conn.autocommit = False
    return conn


def quote_val(v):
    """Retorna placeholder %s — psycopg2 faz o escape corretamente."""
    return "%s"


def convert_row(table, row):
    bool_cols = BOOL_COLUMNS.get(table, set())
    result = {}
    for k, v in row.items():
        if k in bool_cols and v is not None:
            result[k] = bool(v)
        else:
            result[k] = v
    return result


def import_table(pg_conn, table, rows):
    if not rows:
        print(f"  {table}: 0 registros (pulando)")
        return

    from psycopg2.extras import execute_values

    cols = list(rows[0].keys())
    col_str = ", ".join(f'"{c}"' for c in cols)
    placeholders = ", ".join(["%s"] * len(cols))

    cur = pg_conn.cursor()

    # Limpa tabela antes de inserir (preserva estrutura)
    cur.execute(f'DELETE FROM "{table}"')

    rows = [convert_row(table, r) for r in rows]
    values = [tuple(row[c] for c in cols) for row in rows]
    sql = f'INSERT INTO "{table}" ({col_str}) VALUES ({placeholders})'

    for row_vals in values:
        cur.execute(sql, row_vals)

    print(f"  {table}: {len(rows)} registros importados")


def reset_sequences(pg_conn):
    """Atualiza sequences das PKs para o valor máximo após importação."""
    cur = pg_conn.cursor()
    sequence_tables = {
        "condominios": "id",
        "saloes": "id",
        "reservas": "id",
        "historico": "id",
        "creditos": "id",
        "bloqueios": "id",
        "feriados": "id",
        "usuarios": "id",
        "itens_inventario": "id",
        "regras_regimento": "id",
        "documentos_gerados": "id",
    }
    for table, col in sequence_tables.items():
        cur.execute(f"""
            SELECT setval(
                pg_get_serial_sequence('{table}', '{col}'),
                COALESCE((SELECT MAX({col}) FROM "{table}"), 1)
            )
        """)
    print("  Sequences atualizadas.")


def import_to_pg(data):
    print(f"\n[IMPORT] Conectando ao PostgreSQL (Supabase)...")
    pg_conn = pg_connect()
    print("[IMPORT] Conexão OK.")

    try:
        # Desabilita verificação de FK temporariamente para facilitar a ordem
        cur = pg_conn.cursor()
        cur.execute("SET session_replication_role = replica;")

        for table in TABLES_ORDER:
            rows = data.get(table, [])
            import_table(pg_conn, table, rows)

        cur.execute("SET session_replication_role = DEFAULT;")
        reset_sequences(pg_conn)

        pg_conn.commit()
        print("[IMPORT] Commit realizado com sucesso.")
    except Exception as e:
        pg_conn.rollback()
        print(f"[IMPORT] ERRO — rollback executado: {e}")
        raise
    finally:
        pg_conn.close()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    backup_file = "sqlite_backup.json"

    print("=" * 60)
    print("MIGRACAO SQLite -> PostgreSQL (Supabase)")
    print("=" * 60)

    # Exporta
    data = export_sqlite(backup_file)

    # Importa
    import_to_pg(data)

    print("\n[OK] Migração concluída com sucesso!")
    print(f"[OK] Backup em: {backup_file} (NUNCA delete este arquivo)")
