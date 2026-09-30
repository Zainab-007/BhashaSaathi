from alembic import op
import sqlalchemy as sa

revision = '0002_sync_user'
down_revision = '0001_initial'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('sync_records') as batch:
        batch.add_column(sa.Column('user_id', sa.Integer(), nullable=True))
        batch.create_index('ix_sync_records_user_id', ['user_id'], unique=False)
        batch.create_foreign_key('fk_sync_records_user_id_users', 'users', ['user_id'], ['id'])
    op.execute("UPDATE sync_records SET user_id = (SELECT id FROM users ORDER BY id LIMIT 1) WHERE user_id IS NULL")
    with op.batch_alter_table('sync_records') as batch:
        batch.alter_column('user_id', existing_type=sa.Integer(), nullable=False)
        batch.create_unique_constraint('uq_sync_user_device_operation_entity', ['user_id', 'device_id', 'operation', 'entity_id'])


def downgrade():
    with op.batch_alter_table('sync_records') as batch:
        batch.drop_constraint('uq_sync_user_device_operation_entity', type_='unique')
        batch.drop_constraint('fk_sync_records_user_id_users', type_='foreignkey')
        batch.drop_index('ix_sync_records_user_id')
        batch.drop_column('user_id')
