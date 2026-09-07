"""add_salao_id_reserva_anual_unidade

[FIX] Cota anual de isenção era contada só por condominio_id+apartamento+ano,
sem olhar salao_id — mas a RegraPrecificacao já é cadastrada por salão
específico. Num condomínio com 2 salões com regra separada, usar a isenção
num consumia a cota do outro. Adiciona salao_id e faz backfill a partir da
reserva já vinculada (reserva.salao_id), pra registros já existentes não
ficarem com a coluna vazia.

Revision ID: e3f4a5b6c7d8
Revises: d2e3f4a5b6c7
Create Date: 2026-07-24 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

revision      = 'e3f4a5b6c7d8'
down_revision = 'd2e3f4a5b6c7'
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
    if 'salao_id' not in _column_names('reserva_anual_unidade'):
        op.add_column('reserva_anual_unidade', sa.Column('salao_id', sa.Integer(), sa.ForeignKey('saloes.id'), nullable=True))

    # Backfill: preenche salao_id dos registros existentes a partir da
    # reserva já vinculada. Subquery correlacionada (não UPDATE...FROM) pra
    # funcionar em Postgres (produção) e SQLite (dev local) sem diferenciar.
    op.execute(sa.text("""
        UPDATE reserva_anual_unidade
        SET salao_id = (
            SELECT reservas.salao_id FROM reservas
            WHERE reservas.id = reserva_anual_unidade.reserva_id
        )
        WHERE salao_id IS NULL
    """))

    if 'ix_reserva_anual_unidade_salao_id' not in _index_names('reserva_anual_unidade'):
        op.create_index('ix_reserva_anual_unidade_salao_id', 'reserva_anual_unidade', ['salao_id'])


def downgrade():
    try:
        op.drop_index('ix_reserva_anual_unidade_salao_id', table_name='reserva_anual_unidade')
    except Exception:
        pass
    op.drop_column('reserva_anual_unidade', 'salao_id')
