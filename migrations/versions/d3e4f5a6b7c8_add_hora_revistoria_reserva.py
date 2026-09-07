"""add_hora_revistoria_reserva

Revision ID: d3e4f5a6b7c8
Revises: c7d8e9f0a1b2
Create Date: 2026-08-13 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

revision      = 'd3e4f5a6b7c8'
down_revision = 'c7d8e9f0a1b2'
branch_labels = None
depends_on    = None


def upgrade():
    # [FEATURE] horário da revistoria combinado com o morador durante a
    # vistoria — o fiscal preenche no formulário de vistoria (antes da
    # festa), fica salvo na própria reserva, igual hora_vistoria.
    op.add_column(
        'reservas',
        sa.Column('hora_revistoria', sa.String(length=5), nullable=True)
    )


def downgrade():
    op.drop_column('reservas', 'hora_revistoria')
