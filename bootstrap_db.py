"""
bootstrap_db.py — cria o schema do zero a partir dos models (atalho de dev).

As migrations do projeto contêm SQL Postgres-only (CREATE INDEX CONCURRENTLY,
UPDATE ... FROM, ALTER COLUMN ... DROP DEFAULT), então `flask db upgrade` a
partir de um banco vazio quebra em SQLite. Este script cria todas as tabelas
direto do metadata do SQLAlchemy e marca o Alembic como "no head", para que
`flask db upgrade` posterior seja um no-op em vez de tentar reaplicar tudo.

Em produção (PostgreSQL) NÃO use isto — o Procfile já roda `flask db upgrade`,
que é a fonte de verdade do schema.

Uso:
    python bootstrap_db.py
"""
from dotenv import load_dotenv

load_dotenv(override=True)

from flask_migrate import stamp

from app import create_app, db

app = create_app()

with app.app_context():
    db.create_all()
    print(f"Tabelas criadas em: {db.engine.url}")
    print(f"Total de tabelas: {len(db.metadata.tables)}")

    # Alinha o Alembic com o schema recém-criado (evita que um
    # `flask db upgrade` futuro tente rodar as migrations Postgres-only).
    stamp()
    print("Alembic marcado no head.")
