"""add fiscal_fixo e termo_fotografico

Revision ID: e9f0a1b2c3d4
Revises: a4b5c6d7e8f9
Create Date: 2026-08-15

Mudanças:
- condominios: +termo_fotografico (Boolean, default False)
- usuarios: perfil 'fiscal_fixo' é só um valor de string — sem schema change
- itens_inventario_fotos: nova tabela (foto Cloudinary por item de inventário)
"""

from alembic import op
import sqlalchemy as sa

revision = 'e9f0a1b2c3d4'
down_revision = 'b5c6d7e8f9a0'
branch_labels = None
depends_on = None


def upgrade():
    # ── condominios: flag de termo fotográfico ──────────────────────────────
    op.add_column(
        'condominios',
        sa.Column('termo_fotografico', sa.Boolean(), nullable=False,
                  server_default=sa.false())
    )

    # ── itens_inventario_fotos: nova tabela ─────────────────────────────────
    op.create_table(
        'itens_inventario_fotos',
        sa.Column('id',                  sa.Integer(),     nullable=False),
        sa.Column('item_inventario_id',  sa.Integer(),     nullable=False),
        sa.Column('cloudinary_url',      sa.String(500),   nullable=False),
        sa.Column('cloudinary_public_id',sa.String(300),   nullable=True),
        sa.Column('descricao',           sa.String(200),   nullable=True),
        sa.Column('observacao',          sa.Text(),        nullable=True),
        sa.Column('criado_em',           sa.DateTime(),    nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(
            ['item_inventario_id'], ['itens_inventario.id'],
            name='fk_item_inventario_foto',
            ondelete='CASCADE',
        ),
    )
    op.create_index(
        'ix_itens_inventario_fotos_item_id',
        'itens_inventario_fotos',
        ['item_inventario_id'],
    )


def downgrade():
    op.drop_index('ix_itens_inventario_fotos_item_id', table_name='itens_inventario_fotos')
    op.drop_table('itens_inventario_fotos')
    op.drop_column('condominios', 'termo_fotografico')
