"""add_vistoria_mobile_tables

[TESTE] Feature de vistoria digital via celular. Ver CHANGELOG_VISTORIA_MOBILE.md
(raiz do projeto) para instruções de rollback caso o teste não emplaque.

NOTA: guardas de idempotência (checkfirst) porque app/__init__.py roda
db.create_all() a cada boot do app factory — inclusive quando o próprio
`flask db upgrade` sobe o app pra rodar este arquivo. Sem a guarda, a tabela
já existe (criada pelo create_all) quando o Alembic tenta criá-la de novo
e o deploy quebra com DuplicateTable. Isso é um problema estrutural
pré-existente do projeto (create_all + migração de tabela nova sempre colide),
não específico desta feature.

Revision ID: b7c8d9e0f1a2
Revises: d4e5f6a7b8c9
Create Date: 2026-07-16 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

revision      = 'b7c8d9e0f1a2'
down_revision = 'd4e5f6a7b8c9'
branch_labels = None
depends_on    = None


def _insp():
    return sa.inspect(op.get_bind())


def _index_names(tabela):
    if tabela not in _insp().get_table_names():
        return set()
    return {ix['name'] for ix in _insp().get_indexes(tabela)}


def upgrade():
    if 'vistoria_termos' not in _insp().get_table_names():
        op.create_table(
            'vistoria_termos',
            sa.Column('id', sa.Integer(), primary_key=True),
            sa.Column('reserva_id', sa.Integer(), sa.ForeignKey('reservas.id'), nullable=False),
            sa.Column('momento', sa.String(10), nullable=False),
            sa.Column('usuario_id', sa.Integer(), sa.ForeignKey('usuarios.id'), nullable=False),
            sa.Column('assinatura_fiscal_b64', sa.Text(), nullable=False),
            sa.Column('assinatura_requerente_b64', sa.Text(), nullable=False),
            sa.Column('nome_assinante_requerente', sa.String(150), nullable=True),
            sa.Column('observacoes', sa.Text(), nullable=True),
            sa.Column('preenchido_em', sa.DateTime(), nullable=True),
            sa.UniqueConstraint('reserva_id', 'momento', name='uq_vistoria_reserva_momento'),
        )
    if 'ix_vistoria_termos_reserva_id' not in _index_names('vistoria_termos'):
        op.create_index('ix_vistoria_termos_reserva_id', 'vistoria_termos', ['reserva_id'])

    if 'vistoria_itens' not in _insp().get_table_names():
        op.create_table(
            'vistoria_itens',
            sa.Column('id', sa.Integer(), primary_key=True),
            sa.Column('vistoria_termo_id', sa.Integer(), sa.ForeignKey('vistoria_termos.id'), nullable=False),
            sa.Column('item_inventario_id', sa.Integer(), sa.ForeignKey('itens_inventario.id'), nullable=False),
            sa.Column('presente', sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column('observacao', sa.String(300), nullable=True),
        )
    if 'ix_vistoria_itens_vistoria_termo_id' not in _index_names('vistoria_itens'):
        op.create_index('ix_vistoria_itens_vistoria_termo_id', 'vistoria_itens', ['vistoria_termo_id'])


def downgrade():
    try:
        op.drop_index('ix_vistoria_itens_vistoria_termo_id', table_name='vistoria_itens')
    except Exception:
        pass
    op.drop_table('vistoria_itens')
    try:
        op.drop_index('ix_vistoria_termos_reserva_id', table_name='vistoria_termos')
    except Exception:
        pass
    op.drop_table('vistoria_termos')
