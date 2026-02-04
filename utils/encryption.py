"""
Encryption utility for API keys using Fernet symmetric encryption
"""
import os
from pathlib import Path
from cryptography.fernet import Fernet
from typing import Optional

# Load .env file
from dotenv import load_dotenv
env_path = Path(__file__).parent.parent / '.env'
load_dotenv(env_path)


def get_encryption_key() -> bytes:
    """Get or generate encryption key from environment"""
    key = os.getenv("ENCRYPTION_KEY")
    if key:
        return key.encode()
    
    # Generate new key if not exists (for development only)
    new_key = Fernet.generate_key()
    print(f"⚠️  Generated new ENCRYPTION_KEY: {new_key.decode()}")
    print("Please set this as ENCRYPTION_KEY environment variable for production!")
    return new_key


class Encryptor:
    """Handles encryption and decryption of sensitive data"""
    
    def __init__(self):
        self._key = get_encryption_key()
        self._fernet = Fernet(self._key)
    
    def encrypt(self, data: str) -> str:
        """Encrypt string data and return base64 encoded result"""
        if not data:
            return ""
        encrypted = self._fernet.encrypt(data.encode())
        return encrypted.decode()
    
    def decrypt(self, encrypted_data: str) -> str:
        """Decrypt base64 encoded data and return original string"""
        if not encrypted_data:
            return ""
        try:
            decrypted = self._fernet.decrypt(encrypted_data.encode())
            return decrypted.decode()
        except Exception as e:
            raise ValueError(f"Decryption failed: {e}")
    
    def mask_key(self, key: str, visible_chars: int = 4) -> str:
        """Mask API key for display purposes"""
        if not key or len(key) <= visible_chars * 2:
            return "*" * 8
        return f"{key[:visible_chars]}{'*' * (len(key) - visible_chars * 2)}{key[-visible_chars:]}"


# Global encryptor instance
encryptor = Encryptor()
