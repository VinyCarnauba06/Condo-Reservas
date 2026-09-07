"""add_ativo_item_inventario

Revision ID: e5f6a7b8c9d0
Revises: d1e2f3a4b5c6
Create Date: 2026-08-13 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

revision      = 'e5f6a7b8c9d0'
down_revision = 'd1e2f3a4b5c6'
branch_labels = None
depends_on    = None


def upgrade():
    # [FIX] admin_inventario() fazia delete-all + recreate a cada save, o
    # que quebra com ForeignKeyViolation assim que algum item já tem
    # VistoriaItem referenciando ele (vistoria digital preenchida). Soft
    # delete via ativo=False resolve sem perder histórico.
    op.add_column(
        'itens_inventario',
        sa.Column('ativo', sa.Boolean(), nullable=False, server_default=sa.true())
    )
    op.alter_column('itens_inventario', 'ativo', server_default=None)


def downgrade():
    op.drop_column('itens_inventario', 'ativo')
