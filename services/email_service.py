"""
Email Service - Send emails via SMTP
"""
import smtplib
import os
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from typing import Optional
from pathlib import Path

# Load .env file
from dotenv import load_dotenv
env_path = Path(__file__).parent.parent / '.env'
load_dotenv(env_path)

from utils.logger import setup_logger

logger = setup_logger(__name__)


class EmailService:
    """Email sending service using SMTP"""
    
    def __init__(self):
        self.smtp_host = os.getenv("SMTP_HOST", "smtp.gmail.com")
        self.smtp_port = int(os.getenv("SMTP_PORT", "587"))
        self.smtp_user = os.getenv("SMTP_USER", "")
        self.smtp_password = os.getenv("SMTP_PASSWORD", "")
        self.from_email = os.getenv("SMTP_FROM_EMAIL", self.smtp_user)
        self.from_name = os.getenv("SMTP_FROM_NAME", "Upbit Auto Trading")
    
    def is_configured(self) -> bool:
        """Check if SMTP is configured"""
        return bool(self.smtp_user and self.smtp_password)
    
    def send_email(
        self,
        to_email: str,
        subject: str,
        html_content: str,
        text_content: Optional[str] = None
    ) -> bool:
        """
        Send an email
        
        Args:
            to_email: Recipient email address
            subject: Email subject
            html_content: HTML body content
            text_content: Plain text body (optional)
            
        Returns:
            True if sent successfully, False otherwise
        """
        if not self.is_configured():
            logger.warning("SMTP not configured. Set SMTP_USER and SMTP_PASSWORD environment variables.")
            return False
        
        try:
            # Create message
            msg = MIMEMultipart("alternative")
            msg["Subject"] = subject
            msg["From"] = f"{self.from_name} <{self.from_email}>"
            msg["To"] = to_email
            
            # Add plain text version
            if text_content:
                part1 = MIMEText(text_content, "plain", "utf-8")
                msg.attach(part1)
            
            # Add HTML version
            part2 = MIMEText(html_content, "html", "utf-8")
            msg.attach(part2)
            
            # Send email
            with smtplib.SMTP(self.smtp_host, self.smtp_port) as server:
                server.starttls()
                server.login(self.smtp_user, self.smtp_password)
                server.sendmail(self.from_email, to_email, msg.as_string())
            
            logger.info(f"Email sent successfully to {to_email}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to send email to {to_email}: {e}")
            return False
    
    def send_password_reset_email(self, to_email: str, temp_password: str) -> bool:
        """
        Send password reset email with temporary password
        
        Args:
            to_email: User's email address
            temp_password: Temporary password
            
        Returns:
            True if sent successfully
        """
        subject = "[Upbit Auto Trading] 비밀번호 재설정 안내"
        
        html_content = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="UTF-8">
            <style>
                body {{ font-family: 'Apple SD Gothic Neo', 'Malgun Gothic', sans-serif; background-color: #1a1a2e; color: #ffffff; padding: 20px; }}
                .container {{ max-width: 600px; margin: 0 auto; background-color: #16213e; border-radius: 16px; padding: 40px; }}
                .header {{ text-align: center; margin-bottom: 30px; }}
                .logo {{ width: 60px; height: 60px; background: linear-gradient(135deg, #3b82f6, #8b5cf6); border-radius: 12px; margin: 0 auto 16px; display: flex; align-items: center; justify-content: center; }}
                h1 {{ color: #ffffff; font-size: 24px; margin: 0; }}
                .content {{ background-color: #1a1a2e; border-radius: 12px; padding: 24px; margin: 20px 0; }}
                .password {{ font-size: 28px; font-weight: bold; color: #3b82f6; letter-spacing: 2px; text-align: center; padding: 16px; background-color: #0f172a; border-radius: 8px; font-family: monospace; }}
                .warning {{ background-color: #fef3c7; color: #92400e; border-radius: 8px; padding: 16px; margin-top: 20px; }}
                .footer {{ text-align: center; color: #6b7280; font-size: 12px; margin-top: 30px; }}
            </style>
        </head>
        <body>
            <div class="container">
                <div class="header">
                    <div class="logo">📈</div>
                    <h1>비밀번호 재설정</h1>
                </div>
                
                <p>안녕하세요,</p>
                <p>비밀번호 재설정 요청이 접수되어 임시 비밀번호를 발급해 드립니다.</p>
                
                <div class="content">
                    <p style="text-align: center; margin-bottom: 12px; color: #9ca3af;">임시 비밀번호</p>
                    <div class="password">{temp_password}</div>
                </div>
                
                <div class="warning">
                    ⚠️ <strong>보안 안내</strong><br>
                    로그인 후 반드시 새 비밀번호로 변경해 주세요.
                </div>
                
                <div class="footer">
                    <p>본 메일은 발신 전용입니다.</p>
                    <p>© Upbit Auto Trading System</p>
                </div>
            </div>
        </body>
        </html>
        """
        
        text_content = f"""
        [Upbit Auto Trading] 비밀번호 재설정 안내
        
        안녕하세요,
        
        비밀번호 재설정 요청이 접수되어 임시 비밀번호를 발급해 드립니다.
        
        임시 비밀번호: {temp_password}
        
        ⚠️ 로그인 후 반드시 새 비밀번호로 변경해 주세요.
        
        본 메일은 발신 전용입니다.
        """
        
        return self.send_email(to_email, subject, html_content, text_content)


# Global instance
email_service = EmailService()
