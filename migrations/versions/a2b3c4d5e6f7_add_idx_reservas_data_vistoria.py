"""add_idx_reservas_data_vistoria

Revision ID: a2b3c4d5e6f7
Revises: e7f8a9b0c1d2
Create Date: 2026-06-05 00:00:00.000000

"""
from alembic import op

revision      = 'a2b3c4d5e6f7'
down_revision = 'e7f8a9b0c1d2'
branch_labels = None
depends_on    = None


def upgrade():
    # CREATE INDEX CONCURRENTLY não pode rodar dentro de uma transaction
    op.execute('COMMIT')
    op.execute(
        'CREATE INDEX CONCURRENTLY IF NOT EXISTS '
        'idx_reservas_data_vistoria ON reservas(data_vistoria)'
    )


def downgrade():
    op.execute('DROP INDEX IF EXISTS idx_reservas_data_vistoria')
