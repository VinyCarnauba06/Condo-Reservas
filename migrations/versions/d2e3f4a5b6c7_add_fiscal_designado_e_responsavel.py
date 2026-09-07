"""add_fiscal_designado_e_responsavel

[TESTE] Roteamento de vistoria por fiscal. Dias de semana: fiscal fixo por
condomínio (Condominio.fiscal_id). Fim de semana: fiscal reivindica a
vistoria que vai cobrir (Reserva.fiscal_responsavel_id). Ver
CHANGELOG_VISTORIA_MOBILE.md (raiz do projeto) para rollback.

Revision ID: d2e3f4a5b6c7
Revises: c1d2e3f4a5b6
Create Date: 2026-07-23 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

revision      = 'd2e3f4a5b6c7'
down_revision = 'c1d2e3f4a5b6'
branch_labels = None
depends_on    = None


def _insp():
    return sa.inspect(op.get_bind())


def _column_names(tabela):
    if tabela not in _insp().get_table_names():
        return set()
    return {c['name'] for c in _insp().get_columns(tabela)}


def _index_names(tabela):
    if tabela not in _insp().get_table_names():
        return set()
    return {ix['name'] for ix in _insp().get_indexes(tabela)}


def upgrade():
    if 'fiscal_id' not in _column_names('condominios'):
        op.add_column('condominios', sa.Column('fiscal_id', sa.Integer(), sa.ForeignKey('usuarios.id'), nullable=True))

    if 'fiscal_responsavel_id' not in _column_names('reservas'):
        op.add_column('reservas', sa.Column('fiscal_responsavel_id', sa.Integer(), sa.ForeignKey('usuarios.id'), nullable=True))
    if 'ix_reservas_fiscal_responsavel_id' not in _index_names('reservas'):
        op.create_index('ix_reservas_fiscal_responsavel_id', 'reservas', ['fiscal_responsavel_id'])


def downgrade():
    try:
        op.drop_index('ix_reservas_fiscal_responsavel_id', table_name='reservas')
    except Exception:
        pass
    op.drop_column('reservas', 'fiscal_responsavel_id')
    op.drop_column('condominios', 'fiscal_id')
