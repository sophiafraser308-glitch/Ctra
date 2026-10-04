#!/usr/bin/env python3
"""Print a new Fernet ENCRYPTION_KEY. Store it ONLY in your environment / secret manager."""
from cryptography.fernet import Fernet

if __name__ == "__main__":
    print(Fernet.generate_key().decode())
