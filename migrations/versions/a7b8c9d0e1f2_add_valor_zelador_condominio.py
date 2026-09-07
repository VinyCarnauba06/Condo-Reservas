"""add_valor_zelador_condominio

Recibo de zelador (limpeza pós-festa) automatizado. Condominio ganha
valor_zelador (quanto cobrar) e obs_zelador (nota livre — ex: "morador paga
ao fiscal", "adicionar ao boleto" — hoje só informativo, sem lógica
condicional ainda). Backfill com a tabela "Pago à Administradora" passada pelo Viny
em 2026-07-24, casando por nome do condomínio (case-insensitive).

Revision ID: a7b8c9d0e1f2
Revises: f1a2b3c4d5e6
Create Date: 2026-07-24 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

revision      = 'a7b8c9d0e1f2'
down_revision = 'f1a2b3c4d5e6'
branch_labels = None
depends_on    = None


def _insp():
    return sa.inspect(op.get_bind())


def _column_names(tabela):
    if tabela not in _insp().get_table_names():
        return set()
    return {c['name'] for c in _insp().get_columns(tabela)}


# Tabela "Pago à Administradora (Enviar apoio)" — nome do condomínio, valor, observação.
_VALORES_ZELADOR = [
    ('Residencial Aurora',      120.00, None),
    ('Edifício Bela Vista',  100.00, 'Morador paga ao fiscal'),
    ('Residencial Palmeiras',     100.00, 'Se optar por limpeza pós-festa'),
    ('Condomínio Girassol',     100.00, None),
    ('Residencial Horizonte',   100.00, None),
    ('Edifício Vista Mar',   120.00, None),
    ('Residencial Monte Verde',      100.00, None),
    ('Condomínio Serra Azul',     60.00, 'Adicionar ao boleto'),
    ('Residencial Jardins',         100.00, None),
    ('Edifício Solar',     100.00, None),
    ('Residencial Atlântico',         100.00, None),
    ('Condomínio Mirante',    100.00, 'Apenas salão'),
    ('Residencial Cristal',            100.00, None),
    ('Edifício Boulevard',             100.00, None),
    ('Residencial Vitória',    50.00, 'Somente sáb/dom; apenas salão'),
]


def upgrade():
    if 'valor_zelador' not in _column_names('condominios'):
        op.add_column('condominios', sa.Column('valor_zelador', sa.Numeric(10, 2), nullable=True))
    if 'obs_zelador' not in _column_names('condominios'):
        op.add_column('condominios', sa.Column('obs_zelador', sa.String(300), nullable=True))

    bind = op.get_bind()
    for nome, valor, obs in _VALORES_ZELADOR:
        bind.execute(
            sa.text(
                'UPDATE condominios SET valor_zelador = :valor, obs_zelador = :obs '
                'WHERE upper(nome) = upper(:nome) AND valor_zelador IS NULL'
            ),
            {'valor': valor, 'obs': obs, 'nome': nome},
        )


def downgrade():
    if 'obs_zelador' in _column_names('condominios'):
        op.drop_column('condominios', 'obs_zelador')
    if 'valor_zelador' in _column_names('condominios'):
        op.drop_column('condominios', 'valor_zelador')
