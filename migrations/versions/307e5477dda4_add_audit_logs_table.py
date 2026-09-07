"""add_audit_logs_table

Revision ID: 307e5477dda4
Revises: 6882bb1dc130
Create Date: 2026-05-14 09:30:47.277119

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '307e5477dda4'
down_revision = '6882bb1dc130'
branch_labels = None
depends_on = None


def upgrade():
    # Criar tabela audit_logs (pode já existir em SQLite via create_all)
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if 'audit_logs' not in inspector.get_table_names():
        op.create_table('audit_logs',
            sa.Column('id',          sa.Integer(),     nullable=False),
            sa.Column('usuario_id',  sa.Integer(),     nullable=True),
            sa.Column('acao',        sa.String(100),   nullable=False),
            sa.Column('tabela',      sa.String(50),    nullable=True),
            sa.Column('registro_id', sa.Integer(),     nullable=True),
            sa.Column('dados_antes', sa.JSON(),        nullable=True),
            sa.Column('dados_depois',sa.JSON(),        nullable=True),
            sa.Column('ip_address',  sa.String(50),    nullable=True),
            sa.Column('user_agent',  sa.String(500),   nullable=True),
            sa.Column('criado_em',   sa.DateTime(),    nullable=True),
            sa.ForeignKeyConstraint(['usuario_id'], ['usuarios.id']),
            sa.PrimaryKeyConstraint('id'),
        )
        op.create_index('ix_audit_logs_acao',      'audit_logs', ['acao'],      unique=False)
        op.create_index('ix_audit_logs_criado_em', 'audit_logs', ['criado_em'], unique=False)

    with op.batch_alter_table('reservas', schema=None) as batch_op:
        batch_op.alter_column('_nome_solicitante_enc',
               existing_type=sa.VARCHAR(length=500),
               nullable=False)
        batch_op.alter_column('_apartamento_enc',
               existing_type=sa.VARCHAR(length=500),
               nullable=False)


def downgrade():
    with op.batch_alter_table('reservas', schema=None) as batch_op:
        batch_op.alter_column('_apartamento_enc',
               existing_type=sa.VARCHAR(length=500),
               nullable=True)
        batch_op.alter_column('_nome_solicitante_enc',
               existing_type=sa.VARCHAR(length=500),
               nullable=True)

    op.drop_index('ix_audit_logs_criado_em', table_name='audit_logs')
    op.drop_index('ix_audit_logs_acao',      table_name='audit_logs')
    op.drop_table('audit_logs')
