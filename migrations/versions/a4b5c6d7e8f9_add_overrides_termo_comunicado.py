"""add_overrides_termo_comunicado

Revision ID: a4b5c6d7e8f9
Revises: 1799fae46632
Create Date: 2026-07-30 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

revision      = 'a4b5c6d7e8f9'
down_revision = '1799fae46632'
branch_labels = None
depends_on    = None


def upgrade():
    with op.batch_alter_table('reservas') as batch_op:
        batch_op.add_column(sa.Column('motivo_festa', sa.String(200), nullable=True))
        batch_op.add_column(sa.Column('data_revistoria_override', sa.Date(), nullable=True))
        batch_op.add_column(sa.Column('comunicado_texto_override', sa.String(500), nullable=True))


def downgrade():
    with op.batch_alter_table('reservas') as batch_op:
        batch_op.drop_column('comunicado_texto_override')
        batch_op.drop_column('data_revistoria_override')
        batch_op.drop_column('motivo_festa')
