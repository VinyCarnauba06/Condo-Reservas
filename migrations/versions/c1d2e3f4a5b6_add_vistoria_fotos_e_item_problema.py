"""add_vistoria_fotos_e_item_problema

[TESTE] Fotos de problema anexadas ao termo + problema persistente por item
de inventário (mantém a obs de "cadeira quebrada" nos próximos termos até
alguém marcar como resolvido). Ver CHANGELOG_VISTORIA_MOBILE.md (raiz do
projeto) para instruções de rollback.

Guardas de idempotência pelo mesmo motivo da migration b7c8d9e0f1a2:
app/__init__.py roda db.create_all() a cada boot, inclusive quando o próprio
`flask db upgrade` sobe o app pra rodar este arquivo.

Revision ID: c1d2e3f4a5b6
Revises: b7c8d9e0f1a2
Create Date: 2026-07-18 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

revision      = 'c1d2e3f4a5b6'
down_revision = 'b7c8d9e0f1a2'
branch_labels = None
depends_on    = None


def _insp():
    return sa.inspect(op.get_bind())


def _index_names(tabela):
    if tabela not in _insp().get_table_names():
        return set()
    return {ix['name'] for ix in _insp().get_indexes(tabela)}


def _constraint_names(tabela):
    if tabela not in _insp().get_table_names():
        return set()
    return {uc['name'] for uc in _insp().get_unique_constraints(tabela)}


def upgrade():
    if 'vistoria_fotos' not in _insp().get_table_names():
        op.create_table(
            'vistoria_fotos',
            sa.Column('id', sa.Integer(), primary_key=True),
            sa.Column('vistoria_termo_id', sa.Integer(), sa.ForeignKey('vistoria_termos.id'), nullable=False),
            sa.Column('foto_data', sa.LargeBinary(), nullable=False),
            sa.Column('descricao', sa.String(200), nullable=True),
            sa.Column('criado_em', sa.DateTime(), nullable=True),
        )
    if 'ix_vistoria_fotos_vistoria_termo_id' not in _index_names('vistoria_fotos'):
        op.create_index('ix_vistoria_fotos_vistoria_termo_id', 'vistoria_fotos', ['vistoria_termo_id'])

    if 'itens_problema' not in _insp().get_table_names():
        op.create_table(
            'itens_problema',
            sa.Column('id', sa.Integer(), primary_key=True),
            sa.Column('salao_id', sa.Integer(), sa.ForeignKey('saloes.id'), nullable=False),
            sa.Column('descricao_item', sa.String(200), nullable=False),
            sa.Column('observacao', sa.Text(), nullable=False),
            sa.Column('verificado', sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column('registrado_em', sa.DateTime(), nullable=True),
            sa.UniqueConstraint('salao_id', 'descricao_item', name='uq_item_problema_salao_desc'),
        )
    if 'ix_itens_problema_salao_id' not in _index_names('itens_problema'):
        op.create_index('ix_itens_problema_salao_id', 'itens_problema', ['salao_id'])
    if 'ix_itens_problema_descricao_item' not in _index_names('itens_problema'):
        op.create_index('ix_itens_problema_descricao_item', 'itens_problema', ['descricao_item'])


def downgrade():
    try:
        op.drop_index('ix_itens_problema_descricao_item', table_name='itens_problema')
    except Exception:
        pass
    try:
        op.drop_index('ix_itens_problema_salao_id', table_name='itens_problema')
    except Exception:
        pass
    op.drop_table('itens_problema')

    try:
        op.drop_index('ix_vistoria_fotos_vistoria_termo_id', table_name='vistoria_fotos')
    except Exception:
        pass
    op.drop_table('vistoria_fotos')
