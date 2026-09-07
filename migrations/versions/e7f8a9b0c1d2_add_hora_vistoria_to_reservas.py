"""add_hora_vistoria_to_reservas

Revision ID: e7f8a9b0c1d2
Revises: f8e9d0c1b2a3
Create Date: 2026-05-29 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

revision      = 'e7f8a9b0c1d2'
down_revision = 'f8e9d0c1b2a3'
branch_labels = None
depends_on    = None


def upgrade():
    with op.batch_alter_table('reservas') as batch_op:
        batch_op.add_column(sa.Column('hora_vistoria', sa.String(5), nullable=True))


def downgrade():
    with op.batch_alter_table('reservas') as batch_op:
        batch_op.drop_column('hora_vistoria')
