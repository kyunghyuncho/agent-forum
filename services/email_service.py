"""
Email service using Resend API for sending verification and notification emails.
"""
import secrets
from datetime import datetime, timedelta
from typing import Optional
import httpx
from sqlalchemy.orm import Session

from database import User, GlobalSettings


class EmailService:
    """Service for sending emails via Resend API."""
    
    RESEND_API_URL = "https://api.resend.com/emails"
    
    def __init__(self, db: Session):
        self.db = db
        self._global_settings: Optional[GlobalSettings] = None
    
    @property
    def global_settings(self) -> Optional[GlobalSettings]:
        """Get cached global settings or fetch from DB."""
        if self._global_settings is None:
            self._global_settings = self.db.query(GlobalSettings).first()
        return self._global_settings
    
    def get_resend_api_key(self) -> Optional[str]:
        """Get Resend API key from global settings."""
        if self.global_settings:
            return self.global_settings.resend_api_key
        return None
    
    def get_app_url(self) -> str:
        """Get the application base URL."""
        if self.global_settings and self.global_settings.app_url:
            return self.global_settings.app_url.rstrip('/')
        return "http://localhost:8000"
    
    def get_from_address(self) -> str:
        """Get the from email address."""
        if self.global_settings:
            name = self.global_settings.email_from_name or "LocalBBS"
            address = self.global_settings.email_from_address or "noreply@localbbs.app"
            return f"{name} <{address}>"
        return "LocalBBS <noreply@localbbs.app>"
    
    def is_email_verification_required(self) -> bool:
        """Check if email verification is required."""
        if self.global_settings:
            return self.global_settings.require_email_verification
        return True  # Default to required
    
    def generate_verification_token(self) -> str:
        """Generate a secure verification token."""
        return secrets.token_urlsafe(32)
    
    async def send_email(
        self,
        to: str,
        subject: str,
        html_content: str,
        text_content: Optional[str] = None
    ) -> bool:
        """
        Send an email using Resend API.
        
        Returns True if successful, False otherwise.
        """
        api_key = self.get_resend_api_key()
        if not api_key:
            print("Warning: No Resend API key configured, email not sent")
            return False
        
        payload = {
            "from": self.get_from_address(),
            "to": [to],
            "subject": subject,
            "html": html_content,
        }
        
        if text_content:
            payload["text"] = text_content
        
        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    self.RESEND_API_URL,
                    headers={
                        "Authorization": f"Bearer {api_key}",
                        "Content-Type": "application/json",
                    },
                    json=payload,
                    timeout=10.0
                )
                
                if response.status_code == 200:
                    print(f"Email sent successfully to {to}")
                    return True
                else:
                    print(f"Failed to send email: {response.status_code} - {response.text}")
                    return False
                    
        except Exception as e:
            print(f"Error sending email: {e}")
            return False
    
    async def send_verification_email(self, user: User) -> bool:
        """
        Send a verification email to a user.
        
        Generates a new token, saves it to the user, and sends the email.
        """
        # Generate token and set expiry (24 hours)
        token = self.generate_verification_token()
        user.verification_token = token
        user.verification_token_expires = datetime.utcnow() + timedelta(hours=24)
        self.db.commit()
        
        # Build verification URL
        verify_url = f"{self.get_app_url()}/verify-email?token={token}"
        
        app_name = "LocalBBS"
        if self.global_settings and self.global_settings.app_name:
            app_name = self.global_settings.app_name
        
        # Email content
        html_content = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="utf-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
        </head>
        <body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; line-height: 1.6; color: #333; max-width: 600px; margin: 0 auto; padding: 20px;">
            <div style="background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); padding: 30px; border-radius: 10px 10px 0 0; text-align: center;">
                <h1 style="color: white; margin: 0; font-size: 28px;">{app_name}</h1>
            </div>
            <div style="background: #f9fafb; padding: 30px; border-radius: 0 0 10px 10px; border: 1px solid #e5e7eb; border-top: none;">
                <h2 style="color: #1f2937; margin-top: 0;">Verify Your Email Address</h2>
                <p>Hi{' ' + user.display_name if user.display_name else ''}!</p>
                <p>Thanks for registering. Please verify your email address by clicking the button below:</p>
                <div style="text-align: center; margin: 30px 0;">
                    <a href="{verify_url}" 
                       style="background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); 
                              color: white; 
                              padding: 14px 30px; 
                              text-decoration: none; 
                              border-radius: 8px; 
                              font-weight: 600;
                              display: inline-block;">
                        Verify Email Address
                    </a>
                </div>
                <p style="color: #6b7280; font-size: 14px;">
                    This link will expire in 24 hours. If you didn't create an account, you can safely ignore this email.
                </p>
                <hr style="border: none; border-top: 1px solid #e5e7eb; margin: 20px 0;">
                <p style="color: #9ca3af; font-size: 12px; margin-bottom: 0;">
                    If the button doesn't work, copy and paste this link into your browser:<br>
                    <a href="{verify_url}" style="color: #667eea; word-break: break-all;">{verify_url}</a>
                </p>
            </div>
        </body>
        </html>
        """
        
        text_content = f"""
        Verify Your Email Address
        
        Hi{' ' + user.display_name if user.display_name else ''}!
        
        Thanks for registering for {app_name}. Please verify your email address by visiting this link:
        
        {verify_url}
        
        This link will expire in 24 hours. If you didn't create an account, you can safely ignore this email.
        """
        
        return await self.send_email(
            to=user.email,
            subject=f"Verify your {app_name} account",
            html_content=html_content,
            text_content=text_content
        )
    
    def verify_token(self, token: str) -> Optional[User]:
        """
        Verify a token and return the user if valid.
        
        Returns None if token is invalid or expired.
        """
        user = self.db.query(User).filter(
            User.verification_token == token
        ).first()
        
        if not user:
            return None
        
        # Check if token has expired
        if user.verification_token_expires and user.verification_token_expires < datetime.utcnow():
            return None
        
        return user
    
    def mark_user_verified(self, user: User) -> None:
        """Mark a user as verified and clear the token."""
        user.is_verified = True
        user.verification_token = None
        user.verification_token_expires = None
        self.db.commit()
    
    async def resend_verification_email(self, user: User) -> bool:
        """Resend verification email for a user who hasn't verified yet."""
        if user.is_verified:
            return False
        return await self.send_verification_email(user)


def get_email_service(db: Session) -> EmailService:
    """Factory function to create EmailService instance."""
    return EmailService(db)
