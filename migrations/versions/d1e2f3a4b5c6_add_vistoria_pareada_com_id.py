"""add_vistoria_pareada_com_id

Revision ID: d1e2f3a4b5c6
Revises: c9d0e1f2a3b4
Create Date: 2026-08-11 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

revision      = 'd1e2f3a4b5c6'
down_revision = 'c9d0e1f2a3b4'
branch_labels = None
depends_on    = None


def upgrade():
    # [FEATURE] festa em dias seguidos, mesma unidade/salão — vistoria
    # combinada. FK auto-referencial: aponta pro 1º dia (dona) quando essa
    # reserva é o 2º dia (filho), NULL caso contrário. Ver
    # app/routes/reservas.py (nova_reserva/cancelar_reserva) e
    # app/routes/relatorios.py (story_reserva/gerar_pdf_termos).
    op.add_column(
        'reservas',
        sa.Column('vistoria_pareada_com_id', sa.Integer(), nullable=True)
    )
    op.create_foreign_key(
        'fk_reservas_vistoria_pareada_com_id',
        'reservas', 'reservas',
        ['vistoria_pareada_com_id'], ['id']
    )
    op.create_index(
        'ix_reservas_vistoria_pareada_com_id',
        'reservas', ['vistoria_pareada_com_id']
    )


def downgrade():
    op.drop_index('ix_reservas_vistoria_pareada_com_id', table_name='reservas')
    op.drop_constraint('fk_reservas_vistoria_pareada_com_id', 'reservas', type_='foreignkey')
    op.drop_column('reservas', 'vistoria_pareada_com_id')
