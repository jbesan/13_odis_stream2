import os

secrets_dir = "/app/.streamlit"
os.makedirs(secrets_dir, exist_ok=True)
secrets_file = os.path.join(secrets_dir, "secrets.toml")

client_id = os.getenv("OIDC_CLIENT_ID", "")
client_secret = os.getenv("OIDC_CLIENT_SECRET", "")
cookie_secret = os.getenv("OIDC_COOKIE_SECRET", "")
redirect_uri = os.getenv("OIDC_REDIRECT_URI", "")

with open(secrets_file, "w") as f:
    f.write("[auth]\n")
    f.write(f'redirect_uri = "{redirect_uri}"\n')
    f.write(f'cookie_secret = "{cookie_secret}"\n\n')
    f.write("[auth.google]\n")
    f.write(f'client_id = "{client_id}"\n')
    f.write(f'client_secret = "{client_secret}"\n')
    f.write(
        'server_metadata_url = "https://accounts.google.com/.well-known/openid-configuration"\n'
    )

print(f"Dynamically generated {secrets_file} from environment variables.")
