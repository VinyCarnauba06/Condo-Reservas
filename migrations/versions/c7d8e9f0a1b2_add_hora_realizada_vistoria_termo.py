"""add_hora_realizada_vistoria_termo

Revision ID: c7d8e9f0a1b2
Revises: e5f6a7b8c9d0
Create Date: 2026-08-13 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

revision      = 'c7d8e9f0a1b2'
down_revision = 'e5f6a7b8c9d0'
branch_labels = None
depends_on    = None


def upgrade():
    # [FEATURE] pedido dos fiscais: horário manual (HH:MM) de quando a
    # vistoria/revistoria foi realizada de fato, pra aparecer no termo ao
    # lado do horário marcado.
    op.add_column(
        'vistoria_termos',
        sa.Column('hora_realizada', sa.String(length=5), nullable=True)
    )


def downgrade():
    op.drop_column('vistoria_termos', 'hora_realizada')
