from werkzeug.security import generate_password_hash
import sys

def main():
    if len(sys.argv) != 2:
        print("Usage: python hash_password.py <your_password_here>")
        sys.exit(1)
    
    password = sys.argv[1]
    hashed_password = generate_password_hash(password)
    
    print("\nYour securely hashed password is:")
    print(hashed_password)
    print("\nCopy the entire string (starting with 'pbkdf2:sha256...') and paste it into your config.json file.\n")

if __name__ == "__main__":
    main() 