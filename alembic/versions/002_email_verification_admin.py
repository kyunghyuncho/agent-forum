"""Add email verification, admin, and global settings

Revision ID: 002_email_verification_admin
Revises: 001_initial_multiuser_schema
Create Date: 2026-02-05

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '002_email_verification_admin'
down_revision = '001_initial'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add new columns to users table
    op.add_column('users', sa.Column('is_admin', sa.Boolean(), nullable=True, server_default='false'))
    op.add_column('users', sa.Column('is_verified', sa.Boolean(), nullable=True, server_default='false'))
    op.add_column('users', sa.Column('verification_token', sa.String(255), nullable=True))
    op.add_column('users', sa.Column('verification_token_expires', sa.DateTime(), nullable=True))
    
    # Create unique index on verification_token
    op.create_index('ix_users_verification_token', 'users', ['verification_token'], unique=True)
    
    # Remove google_safe_browsing_api_key from user_settings (moving to global)
    # Check if column exists first (it may not exist in fresh installations)
    try:
        op.drop_column('user_settings', 'google_safe_browsing_api_key')
    except Exception:
        pass  # Column may not exist
    
    # Create global_settings table
    op.create_table(
        'global_settings',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('google_safe_browsing_api_key', sa.String(255), nullable=True),
        sa.Column('resend_api_key', sa.String(255), nullable=True),
        sa.Column('email_from_address', sa.String(255), nullable=True, server_default='noreply@localbbs.app'),
        sa.Column('email_from_name', sa.String(100), nullable=True, server_default='LocalBBS'),
        sa.Column('require_email_verification', sa.Boolean(), nullable=True, server_default='true'),
        sa.Column('allow_registration', sa.Boolean(), nullable=True, server_default='true'),
        sa.Column('app_name', sa.String(100), nullable=True, server_default='LocalBBS'),
        sa.Column('app_url', sa.String(255), nullable=True),
        sa.Column('max_simulations_per_user', sa.Integer(), nullable=True, server_default='5'),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.Column('updated_by', sa.Integer(), sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True),
    )
    
    # Mark the first user as admin and verified (if exists)
    op.execute("""
        UPDATE users 
        SET is_admin = true, is_verified = true 
        WHERE id = (SELECT MIN(id) FROM users)
    """)


def downgrade() -> None:
    # Drop global_settings table
    op.drop_table('global_settings')
    
    # Add back google_safe_browsing_api_key to user_settings
    op.add_column('user_settings', sa.Column('google_safe_browsing_api_key', sa.String(255), nullable=True))
    
    # Drop index
    op.drop_index('ix_users_verification_token', table_name='users')
    
    # Remove columns from users table
    op.drop_column('users', 'verification_token_expires')
    op.drop_column('users', 'verification_token')
    op.drop_column('users', 'is_verified')
    op.drop_column('users', 'is_admin')
