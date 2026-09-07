"""add_festa_condominio_flag

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-05-14 12:01:00.000000

"""
from alembic import op
import sqlalchemy as sa


revision = 'b2c3d4e5f6a7'
down_revision = 'a1b2c3d4e5f6'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('reservas') as batch_op:
        batch_op.add_column(sa.Column('festa_condominio', sa.Boolean(), nullable=False, server_default='false'))


def downgrade():
    with op.batch_alter_table('reservas') as batch_op:
        batch_op.drop_column('festa_condominio')
