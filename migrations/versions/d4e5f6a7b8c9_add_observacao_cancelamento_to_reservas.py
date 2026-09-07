"""add_observacao_cancelamento_to_reservas

Revision ID: d4e5f6a7b8c9
Revises: 9f8e7d6c5b4a
Create Date: 2026-07-07 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

revision      = 'd4e5f6a7b8c9'
down_revision = '9f8e7d6c5b4a'
branch_labels = None
depends_on    = None


def upgrade():
    with op.batch_alter_table('reservas') as batch_op:
        batch_op.add_column(sa.Column('observacao_cancelamento', sa.String(500), nullable=True))


def downgrade():
    with op.batch_alter_table('reservas') as batch_op:
        batch_op.drop_column('observacao_cancelamento')
