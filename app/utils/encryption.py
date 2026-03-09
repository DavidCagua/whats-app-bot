import os
import logging
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt
from cryptography.hazmat.backends import default_backend


ALGORITHM = "aes-256-gcm"
IV_LENGTH = 16
AUTH_TAG_LENGTH = 16


def get_encryption_key() -> bytes:
    """Get the encryption key from environment variables."""
    secret = os.getenv('ENCRYPTION_SECRET') or os.getenv('NEXTAUTH_SECRET')
    if not secret:
        raise ValueError("ENCRYPTION_SECRET or NEXTAUTH_SECRET must be set")
    
    # Derive a 32-byte key from the secret using scrypt (matching TypeScript implementation)
    kdf = Scrypt(
        salt=b"salt",  # Must match TypeScript: "salt"
        length=32,
        n=2**14,  # Default scrypt parameters
        r=8,
        p=1,
        backend=default_backend()
    )
    return kdf.derive(secret.encode('utf-8'))


def decrypt(encrypted_text: str) -> str:
    """
    Decrypt text encrypted with AES-256-GCM.
    
    Args:
        encrypted_text: Encrypted string in format "iv:authTag:encrypted"
    
    Returns:
        Decrypted plaintext string
    
    Raises:
        ValueError: If encrypted text format is invalid or decryption fails
    """
    parts = encrypted_text.split(":")
    
    if len(parts) != 3:
        raise ValueError("Invalid encrypted text format")
    
    try:
        iv = bytes.fromhex(parts[0])
        auth_tag = bytes.fromhex(parts[1])
        ciphertext = bytes.fromhex(parts[2])
        
        # Validate lengths
        if len(iv) != IV_LENGTH:
            raise ValueError(f"Invalid IV length: expected {IV_LENGTH}, got {len(iv)}")
        if len(auth_tag) != AUTH_TAG_LENGTH:
            raise ValueError(f"Invalid auth tag length: expected {AUTH_TAG_LENGTH}, got {len(auth_tag)}")
        
        # Get encryption key
        key = get_encryption_key()
        
        # Decrypt using AESGCM
        # Python's AESGCM expects ciphertext + tag concatenated
        ciphertext_with_tag = ciphertext + auth_tag
        aesgcm = AESGCM(key)
        decrypted = aesgcm.decrypt(iv, ciphertext_with_tag, None)
        
        return decrypted.decode('utf-8')
        
    except Exception as e:
        logging.error(f"[ENCRYPTION] Decryption failed: {e}")
        raise ValueError(f"Failed to decrypt: {str(e)}")


def is_encrypted(text: str) -> bool:
    """
    Check if text matches the encrypted format (hex:hex:hex).
    
    Args:
        text: String to check
    
    Returns:
        True if text appears to be encrypted
    """
    parts = text.split(":")
    if len(parts) != 3:
        return False
    
    # Check if parts are valid hex and have correct lengths
    try:
        iv_hex = parts[0]
        tag_hex = parts[1]
        encrypted_hex = parts[2]
        
        # Validate hex format
        if not all(c in '0123456789abcdefABCDEF' for c in iv_hex):
            return False
        if not all(c in '0123456789abcdefABCDEF' for c in tag_hex):
            return False
        if not all(c in '0123456789abcdefABCDEF' for c in encrypted_hex):
            return False
        
        # Validate lengths
        if len(iv_hex) != IV_LENGTH * 2:
            return False
        if len(tag_hex) != AUTH_TAG_LENGTH * 2:
            return False
        
        return True
        
    except Exception:
        return False
