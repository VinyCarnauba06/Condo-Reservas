"""add regra_precificacao e reserva_anual_unidade

Revision ID: b3c4d5e6f7a8
Revises: a2b3c4d5e6f7
Create Date: 2026-06-12
"""
from alembic import op
import sqlalchemy as sa

revision      = 'b3c4d5e6f7a8'
down_revision = 'a2b3c4d5e6f7'
branch_labels = None
depends_on    = None


def upgrade():
    op.create_table('regra_precificacao',
        sa.Column('id',              sa.Integer(),     primary_key=True),
        sa.Column('salao_id',        sa.Integer(),     sa.ForeignKey('saloes.id'), nullable=False, unique=True),
        sa.Column('limite_reservas', sa.Integer(),     nullable=False, server_default='1'),
        sa.Column('valor_opcional',  sa.Numeric(10,2), nullable=True),
        sa.Column('descricao',       sa.String(200),   nullable=True),
        sa.Column('ativo',           sa.Boolean(),     nullable=False, server_default='true'),
        sa.Column('criado_em',       sa.DateTime(),    server_default=sa.func.now()),
    )
    op.create_table('reserva_anual_unidade',
        sa.Column('id',                   sa.Integer(),  primary_key=True),
        sa.Column('condominio_id',        sa.Integer(),  sa.ForeignKey('condominios.id'), nullable=False),
        sa.Column('apartamento',          sa.String(20), nullable=False),
        sa.Column('ano',                  sa.Integer(),  nullable=False),
        sa.Column('reserva_id',           sa.Integer(),  sa.ForeignKey('reservas.id'), nullable=False),
        sa.Column('valor_opcional_usado', sa.Boolean(),  nullable=False, server_default='false'),
        sa.Column('criado_em',            sa.DateTime(), server_default=sa.func.now()),
        sa.UniqueConstraint('condominio_id', 'apartamento', 'reserva_id', name='uq_reserva_anual_unidade'),
    )
    op.create_index('ix_rau_cond_apto_ano', 'reserva_anual_unidade', ['condominio_id', 'apartamento', 'ano'])


def downgrade():
    op.drop_index('ix_rau_cond_apto_ano', table_name='reserva_anual_unidade')
    op.drop_table('reserva_anual_unidade')
    op.drop_table('regra_precificacao')
