"""add_emissao_flags_condominio

Revision ID: a1b2c3d4e5f6
Revises: 307e5477dda4
Create Date: 2026-05-14 12:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


revision = 'a1b2c3d4e5f6'
down_revision = 'dd197b4a9d23'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('condominios') as batch_op:
        batch_op.add_column(sa.Column('emite_comunicado', sa.Boolean(), nullable=False, server_default='true'))
        batch_op.add_column(sa.Column('emite_termo', sa.Boolean(), nullable=False, server_default='true'))


def downgrade():
    with op.batch_alter_table('condominios') as batch_op:
        batch_op.drop_column('emite_termo')
        batch_op.drop_column('emite_comunicado')
