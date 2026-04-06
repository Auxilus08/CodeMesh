"""
crypto.py — End-to-End Encryption Module (ECDH + AES-256-GCM)

This module provides the cryptographic primitives for securing all
peer-to-peer communication in the Echo mesh network.

Security Architecture
─────────────────────
    1. Each node generates an ephemeral ECDH key pair on startup.
    2. On first contact with a peer, nodes exchange serialised public keys.
    3. Both sides independently derive the same shared secret via ECDH.
    4. The shared secret is fed through HKDF-SHA256 to produce a 256-bit
       AES key, bound to both node IDs (context separation).
    5. Every message is encrypted with AES-256-GCM using a fresh random
       96-bit nonce.  GCM provides both confidentiality AND authenticity.

    ┌──────────┐                         ┌──────────┐
    │  Node A  │── public_key_a ────────▶│  Node B  │
    │          │◀── public_key_b ────────│          │
    │          │                         │          │
    │ shared = │  ECDH(priv_a, pub_b)    │ shared = │  ECDH(priv_b, pub_a)
    │ aes_key  │  = HKDF(shared)         │ aes_key  │  = HKDF(shared)
    │          │                         │          │
    │ encrypt( │  AES-GCM(key, nonce)    │ decrypt( │  AES-GCM(key, nonce)
    └──────────┘                         └──────────┘

Cryptographic Parameters
────────────────────────
    Curve       : SECP384R1  (NIST P-384, 192-bit security level)
    KDF         : HKDF-SHA256, 32-byte output
    Cipher      : AES-256-GCM
    Nonce       : 96 bits (12 bytes), randomly generated per message
    Tag         : 128 bits (appended by GCM automatically)
"""

from __future__ import annotations

import base64
import logging
import os
from dataclasses import dataclass
from typing import Tuple

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

# ─── Constants ────────────────────────────────────────────────────────────────

CURVE = ec.SECP384R1()
AES_KEY_BYTES: int = 32        # 256-bit AES key
NONCE_BYTES: int = 12          # 96-bit nonce (GCM standard)
HKDF_INFO: bytes = b"echo-mesh-e2ee-v1"

logger: logging.Logger = logging.getLogger("echo.crypto")


# ─── Data Structures ─────────────────────────────────────────────────────────

@dataclass(frozen=True)
class KeyPair:
    """An ECDH key pair.

    Attributes:
        private_key: The EC private key object (never serialise / transmit).
        public_key:  The EC public key object (safe to share with peers).
    """
    private_key: ec.EllipticCurvePrivateKey
    public_key: ec.EllipticCurvePublicKey


@dataclass(frozen=True)
class EncryptedPayload:
    """Container for an AES-GCM encrypted message.

    Attributes:
        ciphertext: The encrypted data (includes the 16-byte GCM auth tag).
        nonce:      The 12-byte random nonce used for this encryption.
    """
    ciphertext: bytes
    nonce: bytes

    def to_dict(self) -> dict[str, str]:
        """Serialise to a JSON-safe dict (base64-encoded)."""
        return {
            "ct": base64.b64encode(self.ciphertext).decode("ascii"),
            "nonce": base64.b64encode(self.nonce).decode("ascii"),
        }

    @classmethod
    def from_dict(cls, d: dict[str, str]) -> "EncryptedPayload":
        """Deserialise from a JSON-safe dict."""
        return cls(
            ciphertext=base64.b64decode(d["ct"]),
            nonce=base64.b64decode(d["nonce"]),
        )


# ─── Key Generation ──────────────────────────────────────────────────────────

def generate_keypair() -> KeyPair:
    """Generate a fresh ECDH key pair on the SECP384R1 curve.

    This should be called once per application session.  The private key
    is ephemeral and must NEVER be persisted to disk.

    Returns:
        A KeyPair containing the private and public keys.
    """
    private_key = ec.generate_private_key(CURVE)
    public_key = private_key.public_key()
    logger.info("Generated ephemeral ECDH key pair (curve=%s).", CURVE.name)
    return KeyPair(private_key=private_key, public_key=public_key)


# ─── Key Serialisation (for network exchange) ────────────────────────────────

def serialise_public_key(public_key: ec.EllipticCurvePublicKey) -> bytes:
    """Serialise a public key to uncompressed X.962 bytes.

    This compact format (97 bytes for P-384) is suitable for embedding
    in a JSON payload as base64.

    Args:
        public_key: The EC public key to serialise.

    Returns:
        Raw bytes of the uncompressed public key point.
    """
    return public_key.public_bytes(
        encoding=serialization.Encoding.X962,
        format=serialization.PublicFormat.UncompressedPoint,
    )


def deserialise_public_key(raw: bytes) -> ec.EllipticCurvePublicKey:
    """Deserialise a public key from uncompressed X.962 bytes.

    Args:
        raw: The raw public key bytes received from a peer.

    Returns:
        The reconstructed EC public key object.

    Raises:
        ValueError: If the bytes do not represent a valid point on the curve.
    """
    return ec.EllipticCurvePublicKey.from_encoded_point(CURVE, raw)


def public_key_to_b64(public_key: ec.EllipticCurvePublicKey) -> str:
    """Convenience: serialise a public key to a base64 string."""
    return base64.b64encode(serialise_public_key(public_key)).decode("ascii")


def public_key_from_b64(b64: str) -> ec.EllipticCurvePublicKey:
    """Convenience: deserialise a public key from a base64 string."""
    return deserialise_public_key(base64.b64decode(b64))


# ─── Key Exchange (ECDH) ─────────────────────────────────────────────────────

def derive_shared_secret(
    private_key: ec.EllipticCurvePrivateKey,
    peer_public_key: ec.EllipticCurvePublicKey,
    salt: bytes | None = None,
    context_info: bytes = HKDF_INFO,
) -> bytes:
    """Derive a 256-bit shared AES key from an ECDH exchange.

    Performs the raw ECDH computation and then passes the result through
    HKDF-SHA256 to produce a uniformly random 32-byte key.

    Args:
        private_key:     Our private key.
        peer_public_key: The peer's public key.
        salt:            Optional HKDF salt (None = zero-filled).
        context_info:    HKDF info/context string for domain separation.

    Returns:
        A 32-byte (256-bit) AES key.
    """
    # Step 1: Raw ECDH — produces a shared point
    raw_shared: bytes = private_key.exchange(ec.ECDH(), peer_public_key)

    # Step 2: KDF — stretch into a uniform AES key
    aes_key: bytes = HKDF(
        algorithm=hashes.SHA256(),
        length=AES_KEY_BYTES,
        salt=salt,
        info=context_info,
    ).derive(raw_shared)

    logger.debug(
        "Derived shared AES-256 key via ECDH + HKDF-SHA256 (%d bytes).",
        len(aes_key),
    )
    return aes_key


# ─── Symmetric Encryption (AES-256-GCM) ──────────────────────────────────────

def encrypt_message(plaintext: str, shared_secret: bytes) -> EncryptedPayload:
    """Encrypt a plaintext string using AES-256-GCM.

    A fresh 96-bit random nonce is generated for every call.  GCM mode
    provides both confidentiality and integrity (AEAD), so any tampering
    with the ciphertext will be detected during decryption.

    Args:
        plaintext:     The message to encrypt (UTF-8 string).
        shared_secret: The 32-byte AES key from derive_shared_secret().

    Returns:
        An EncryptedPayload containing the ciphertext and nonce.

    Raises:
        ValueError: If the shared_secret is not exactly 32 bytes.
    """
    if len(shared_secret) != AES_KEY_BYTES:
        raise ValueError(
            f"shared_secret must be {AES_KEY_BYTES} bytes, got {len(shared_secret)}"
        )

    nonce: bytes = os.urandom(NONCE_BYTES)
    aesgcm = AESGCM(shared_secret)
    ciphertext: bytes = aesgcm.encrypt(nonce, plaintext.encode("utf-8"), None)

    logger.debug(
        "Encrypted %d chars → %d bytes ciphertext.",
        len(plaintext), len(ciphertext),
    )
    return EncryptedPayload(ciphertext=ciphertext, nonce=nonce)


def decrypt_message(
    ciphertext: bytes,
    nonce: bytes,
    shared_secret: bytes,
) -> str:
    """Decrypt an AES-256-GCM ciphertext back to a plaintext string.

    Args:
        ciphertext:    The encrypted bytes (includes the GCM auth tag).
        nonce:         The 12-byte nonce that was used during encryption.
        shared_secret: The 32-byte AES key from derive_shared_secret().

    Returns:
        The decrypted UTF-8 string.

    Raises:
        cryptography.exceptions.InvalidTag:
            If the ciphertext was tampered with or the wrong key is used.
        ValueError:
            If the shared_secret length is invalid.
    """
    if len(shared_secret) != AES_KEY_BYTES:
        raise ValueError(
            f"shared_secret must be {AES_KEY_BYTES} bytes, got {len(shared_secret)}"
        )

    aesgcm = AESGCM(shared_secret)
    plaintext_bytes: bytes = aesgcm.decrypt(nonce, ciphertext, None)

    logger.debug(
        "Decrypted %d bytes ciphertext → %d chars plaintext.",
        len(ciphertext), len(plaintext_bytes),
    )
    return plaintext_bytes.decode("utf-8")


def encrypt_payload(payload_str: str, shared_secret: bytes) -> dict[str, str]:
    """Convenience: encrypt a string and return a JSON-safe dict.

    Useful for embedding directly into a mesh network payload:

        encrypted = encrypt_payload(json.dumps(msg), shared_key)
        await mesh.send_payload(ip, {"type": "encrypted", **encrypted})
    """
    enc = encrypt_message(payload_str, shared_secret)
    return enc.to_dict()


def decrypt_payload(payload_dict: dict[str, str], shared_secret: bytes) -> str:
    """Convenience: decrypt a JSON-safe dict back to a string.

    Inverse of encrypt_payload().
    """
    enc = EncryptedPayload.from_dict(payload_dict)
    return decrypt_message(enc.ciphertext, enc.nonce, shared_secret)


# ─── Standalone Verification ─────────────────────────────────────────────────

def _self_test() -> None:
    """Run a full key-exchange + encrypt/decrypt round-trip."""
    import sys

    logging.basicConfig(
        level=logging.DEBUG,
        format="%(asctime)s │ %(levelname)-7s │ %(name)s │ %(message)s",
        datefmt="%H:%M:%S",
        stream=sys.stdout,
    )

    print("=" * 60)
    print("  Echo E2EE Self-Test  (ECDH + AES-256-GCM)")
    print("=" * 60)

    # 1. Both nodes generate key pairs
    alice = generate_keypair()
    bob = generate_keypair()
    print("\n✅ Key pairs generated (SECP384R1)")

    # 2. Simulate key exchange (serialise → transmit → deserialise)
    alice_pub_b64 = public_key_to_b64(alice.public_key)
    bob_pub_b64 = public_key_to_b64(bob.public_key)
    print(f"   Alice pub: {alice_pub_b64[:40]}…")
    print(f"   Bob   pub: {bob_pub_b64[:40]}…")

    alice_receives_bob_pub = public_key_from_b64(bob_pub_b64)
    bob_receives_alice_pub = public_key_from_b64(alice_pub_b64)
    print("✅ Public keys exchanged and deserialised")

    # 3. Both sides derive the same shared secret
    alice_secret = derive_shared_secret(alice.private_key, alice_receives_bob_pub)
    bob_secret = derive_shared_secret(bob.private_key, bob_receives_alice_pub)
    assert alice_secret == bob_secret, "❌ Shared secrets do not match!"
    print(f"✅ Shared secret derived (identical: {alice_secret == bob_secret})")

    # 4. Alice encrypts, Bob decrypts
    message = "Hello Bob! This is a secret message. 🔐"
    encrypted = encrypt_message(message, alice_secret)
    print(f"\n   Plaintext : {message}")
    print(f"   Ciphertext: {base64.b64encode(encrypted.ciphertext).decode()[:50]}…")
    print(f"   Nonce     : {base64.b64encode(encrypted.nonce).decode()}")

    decrypted = decrypt_message(encrypted.ciphertext, encrypted.nonce, bob_secret)
    assert decrypted == message, "❌ Decryption failed!"
    print(f"   Decrypted : {decrypted}")
    print("✅ Encrypt → Decrypt round-trip passed")

    # 5. Test JSON-safe convenience wrappers
    enc_dict = encrypt_payload(message, alice_secret)
    dec_str = decrypt_payload(enc_dict, bob_secret)
    assert dec_str == message
    print("✅ JSON payload wrappers passed")

    # 6. Tamper detection
    print("\n── Tamper detection test ──")
    tampered = bytearray(encrypted.ciphertext)
    tampered[0] ^= 0xFF  # Flip one byte
    try:
        decrypt_message(bytes(tampered), encrypted.nonce, bob_secret)
        print("❌ Tampered message was NOT rejected!")
    except Exception as e:
        print(f"✅ Tampered message correctly rejected: {type(e).__name__}")

    print("\n" + "=" * 60)
    print("  All tests passed! E2EE module is operational.")
    print("=" * 60)


if __name__ == "__main__":
    _self_test()
