"""Add account-scoped saved summaries without altering existing data."""
from alembic import op
import sqlalchemy as sa

revision = '0007'
down_revision = '0006'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('summary_presets',
        sa.Column('account_id', sa.Text(), primary_key=True), sa.Column('id', sa.Text(), primary_key=True),
        sa.Column('revision', sa.Integer(), nullable=False), sa.Column('archived', sa.Integer(), nullable=False),
        sa.Column('config', sa.Text(), nullable=False), sa.Column('created_at', sa.Text(), nullable=False),
        sa.Column('updated_at', sa.Text(), nullable=False))
    op.create_table('summary_state',
        sa.Column('account_id', sa.Text(), primary_key=True), sa.Column('active_preset_id', sa.Text(), nullable=True),
        sa.Column('active_revision', sa.Integer(), nullable=False), sa.Column('retention_days', sa.Integer(), nullable=False),
        sa.Column('retention_revision', sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(['account_id', 'active_preset_id'], ['summary_presets.account_id', 'summary_presets.id']))
    op.create_table('summary_generations',
        sa.Column('account_id', sa.Text(), primary_key=True), sa.Column('id', sa.Text(), primary_key=True),
        *(sa.Column(k, sa.Text(), nullable=False) for k in ('canonical_config_hash', 'resolved_model', 'prompt_version', 'input_fingerprint', 'markdown', 'generated_at')),
        *(sa.Column(k, sa.Text(), nullable=True) for k in ('actual_response_model', 'window_start', 'window_end', 'covered_oldest', 'covered_newest')),
        *(sa.Column(k, sa.Integer(), nullable=True) for k in ('notification_count', 'sms_count', 'conversation_count', 'latency_ms', 'input_tokens', 'output_tokens', 'cached_input_tokens', 'truncated')),
        sa.UniqueConstraint('account_id', 'canonical_config_hash', 'resolved_model', 'prompt_version', 'input_fingerprint', name='uq_summary_generation_input'))
    op.create_table('summary_entries',
        sa.Column('account_id', sa.Text(), primary_key=True), sa.Column('id', sa.Text(), primary_key=True),
        sa.Column('entry_key', sa.Text(), nullable=False), sa.Column('generation_id', sa.Text(), nullable=False),
        sa.Column('preset_id', sa.Text(), nullable=True), sa.Column('preset_revision', sa.Integer(), nullable=True),
        sa.Column('preset_snapshot', sa.Text(), nullable=True), sa.Column('saved_at', sa.Text(), nullable=False),
        sa.Column('origin', sa.Text(), nullable=False), sa.Column('pinned', sa.Integer(), nullable=False),
        sa.Column('reuse_count', sa.Integer(), nullable=False), sa.Column('last_reused_at', sa.Text(), nullable=True),
        sa.UniqueConstraint('account_id', 'entry_key', name='uq_summary_entry_origin'),
        sa.ForeignKeyConstraint(['account_id', 'generation_id'], ['summary_generations.account_id', 'summary_generations.id']),
        sa.ForeignKeyConstraint(['account_id', 'preset_id'], ['summary_presets.account_id', 'summary_presets.id']))
    op.create_index('ix_summary_entries_history', 'summary_entries', ['account_id', 'saved_at', 'id'])
    op.create_index('ix_summary_entries_preset', 'summary_entries', ['account_id', 'preset_id', 'saved_at'])
    op.create_index('ix_summary_entries_retention', 'summary_entries', ['account_id', 'pinned', 'saved_at'])


def downgrade():
    raise RuntimeError('Restore a verified backup to downgrade')
