import sys
import os
import getpass

# Add app/ directory to path
sys.path.append(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app"))
from utils.auth import hash_password

def main():
    """CLI utility to hash a password using the app's PBKDF2 configuration."""
    if len(sys.argv) > 1:
        password = sys.argv[1]
    else:
        password = getpass.getpass("Enter password to hash: ")
        confirm = getpass.getpass("Confirm password: ")
        if password != confirm:
            print("Error: Passwords do not match.")
            sys.exit(1)
            
    hashed = hash_password(password)
    print(f"\nHashed password:\n{hashed}\n")

if __name__ == "__main__":
    main()
