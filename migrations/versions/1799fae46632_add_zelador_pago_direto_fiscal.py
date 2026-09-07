"""add_zelador_pago_direto_fiscal

Recibo de zelador — primeira regra condicional real por observação (Viny
trouxe o exemplo em 2026-07-24): condomínio com "morador paga ao fiscal"
usa um recibo estruturalmente diferente (endereçado ao condômino pelo
nome, com linha extra de assinatura "Condômino / Fiscal"), em vez do
recibo padrão endereçado ao condomínio. Flag booleana em vez de parsear a
string livre de obs_zelador — mais robusto a edição de texto.

Também corrige erro apontado pelo Viny: observação "Adicionar ao boleto"
do Condomínio Serra Azul não é uma regra real, limpa o campo.

Revision ID: 1799fae46632
Revises: a7b8c9d0e1f2
Create Date: 2026-07-24 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

revision      = '1799fae46632'
down_revision = 'a7b8c9d0e1f2'
branch_labels = None
depends_on    = None


def _insp():
    return sa.inspect(op.get_bind())


def _column_names(tabela):
    if tabela not in _insp().get_table_names():
        return set()
    return {c['name'] for c in _insp().get_columns(tabela)}


_PAGA_DIRETO_FISCAL = ['Edifício Bela Vista', 'Residencial Palmeiras']


def upgrade():
    if 'zelador_pago_direto_fiscal' not in _column_names('condominios'):
        op.add_column(
            'condominios',
            sa.Column('zelador_pago_direto_fiscal', sa.Boolean(), nullable=False, server_default=sa.false()),
        )

    bind = op.get_bind()
    for nome in _PAGA_DIRETO_FISCAL:
        bind.execute(
            sa.text(
                'UPDATE condominios SET zelador_pago_direto_fiscal = TRUE '
                'WHERE upper(nome) = upper(:nome)'
            ),
            {'nome': nome},
        )

    # [FIX] "Adicionar ao boleto" no Condomínio Serra Azul não é regra real (erro
    # de anotação apontado pelo Viny) — limpa.
    bind.execute(
        sa.text(
            "UPDATE condominios SET obs_zelador = NULL "
            "WHERE upper(nome) = upper('Condomínio Serra Azul') AND obs_zelador = 'Adicionar ao boleto'"
        )
    )


def downgrade():
    if 'zelador_pago_direto_fiscal' in _column_names('condominios'):
        op.drop_column('condominios', 'zelador_pago_direto_fiscal')
