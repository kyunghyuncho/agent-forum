"""Initial multi-user schema

Revision ID: 001_initial
Revises: 
Create Date: 2026-02-05

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '001_initial'
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Create users table
    op.create_table('users',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('email', sa.String(length=255), nullable=False),
        sa.Column('password_hash', sa.String(length=255), nullable=False),
        sa.Column('display_name', sa.String(length=100), nullable=True),
        sa.Column('openrouter_api_key', sa.String(length=255), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('is_active', sa.Boolean(), nullable=True),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_users_email'), 'users', ['email'], unique=True)
    op.create_index(op.f('ix_users_id'), 'users', ['id'], unique=False)

    # Create user_settings table
    op.create_table('user_settings',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('default_model', sa.String(length=255), nullable=True),
        sa.Column('default_agent_count', sa.Integer(), nullable=True),
        sa.Column('default_max_loops', sa.Integer(), nullable=True),
        sa.Column('default_loop_delay', sa.Float(), nullable=True),
        sa.Column('default_pool_style', sa.String(length=50), nullable=True),
        sa.Column('mother_intervention_threshold', sa.Integer(), nullable=True),
        sa.Column('mother_lookback_k', sa.Integer(), nullable=True),
        sa.Column('enable_web_browse', sa.Boolean(), nullable=True),
        sa.Column('web_browse_safety_mode', sa.String(length=50), nullable=True),
        sa.Column('web_browse_timeout', sa.Integer(), nullable=True),
        sa.Column('google_safe_browsing_api_key', sa.String(length=255), nullable=True),
        sa.Column('theme', sa.String(length=20), nullable=True),
        sa.Column('posts_per_page', sa.Integer(), nullable=True),
        sa.Column('auto_scroll', sa.Boolean(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('user_id')
    )
    op.create_index(op.f('ix_user_settings_id'), 'user_settings', ['id'], unique=False)

    # Create simulations table
    op.create_table('simulations',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('topic', sa.String(length=500), nullable=False),
        sa.Column('language', sa.String(length=50), nullable=True),
        sa.Column('pool_style', sa.String(length=50), nullable=True),
        sa.Column('status', sa.String(length=20), nullable=True),
        sa.Column('loop_count', sa.Integer(), nullable=True),
        sa.Column('consecutive_idle_count', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.Column('model_name', sa.String(length=255), nullable=True),
        sa.Column('max_loops', sa.Integer(), nullable=True),
        sa.Column('loop_delay', sa.Float(), nullable=True),
        sa.Column('agent_count', sa.Integer(), nullable=True),
        sa.Column('enable_web_browse', sa.Boolean(), nullable=True),
        sa.Column('web_browse_safety_mode', sa.String(length=50), nullable=True),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_simulations_id'), 'simulations', ['id'], unique=False)

    # Create threads table (with simulation_id)
    op.create_table('threads',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('simulation_id', sa.Integer(), nullable=True),
        sa.Column('title', sa.String(length=500), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('status', sa.String(length=20), nullable=True),
        sa.ForeignKeyConstraint(['simulation_id'], ['simulations.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_threads_id'), 'threads', ['id'], unique=False)
    op.create_index(op.f('ix_threads_title'), 'threads', ['title'], unique=False)

    # Create posts table
    op.create_table('posts',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('thread_id', sa.Integer(), nullable=True),
        sa.Column('agent_name', sa.String(length=255), nullable=True),
        sa.Column('content', sa.Text(), nullable=True),
        sa.Column('parent_id', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('likes', sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(['thread_id'], ['threads.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_posts_id'), 'posts', ['id'], unique=False)

    # Create agents table
    op.create_table('agents',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('simulation_id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(length=255), nullable=False),
        sa.Column('directory_name', sa.String(length=255), nullable=False),
        sa.Column('agent_md', sa.Text(), nullable=True),
        sa.Column('memory_md', sa.Text(), nullable=True),
        sa.Column('temp_md', sa.Text(), nullable=True),
        sa.Column('last_read_post_id', sa.Integer(), nullable=True),
        sa.Column('status', sa.String(length=20), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['simulation_id'], ['simulations.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_agents_id'), 'agents', ['id'], unique=False)

    # Create legacy simulation_state table (for backward compatibility)
    op.create_table('simulation_state',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('loop_count', sa.Integer(), nullable=True),
        sa.Column('active_agent_count', sa.Integer(), nullable=True),
        sa.PrimaryKeyConstraint('id')
    )


def downgrade() -> None:
    op.drop_table('simulation_state')
    op.drop_index(op.f('ix_agents_id'), table_name='agents')
    op.drop_table('agents')
    op.drop_index(op.f('ix_posts_id'), table_name='posts')
    op.drop_table('posts')
    op.drop_index(op.f('ix_threads_title'), table_name='threads')
    op.drop_index(op.f('ix_threads_id'), table_name='threads')
    op.drop_table('threads')
    op.drop_index(op.f('ix_simulations_id'), table_name='simulations')
    op.drop_table('simulations')
    op.drop_index(op.f('ix_user_settings_id'), table_name='user_settings')
    op.drop_table('user_settings')
    op.drop_index(op.f('ix_users_id'), table_name='users')
    op.drop_index(op.f('ix_users_email'), table_name='users')
    op.drop_table('users')
