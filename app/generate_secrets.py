import json
import os

secrets_dir = "/app/.streamlit"
os.makedirs(secrets_dir, exist_ok=True)
secrets_file = os.path.join(secrets_dir, "secrets.toml")

client_id = os.getenv("OIDC_CLIENT_ID", "")
client_secret = os.getenv("OIDC_CLIENT_SECRET", "")
cookie_secret = os.getenv("OIDC_COOKIE_SECRET", "")
redirect_uri = os.getenv("OIDC_REDIRECT_URI", "")

# Default domain settings
default_domains = ["jaccueille.fr", "lahso.org", "groupe-sos.org", "fondationcos.org"]
default_domain_mapping = {
    "jaccueille.fr": "jaccueille",
    "lahso.org": "emile_aura",
    "groupe-sos.org": "agir33",
    "fondationcos.org": "agir33",
}

# Auth allowlists & mappings — stored as JSON arrays/objects in env vars on Cloud Run
allowed_domains: list = json.loads(
    os.getenv("OIDC_ALLOWED_DOMAINS_JSON", json.dumps(default_domains))
)
allowed_emails: list = json.loads(os.getenv("OIDC_ALLOWED_EMAILS_JSON", "[]"))
admin_users: list = json.loads(os.getenv("ADMIN_USERS_JSON", "[]"))
domain_org_mapping: dict = json.loads(
    os.getenv("OIDC_DOMAIN_ORG_MAPPING_JSON", json.dumps(default_domain_mapping))
)
email_org_mapping: dict = json.loads(os.getenv("OIDC_EMAIL_ORG_MAPPING_JSON", "{}"))

with open(secrets_file, "w") as f:
    f.write("[auth]\n")
    f.write(f'redirect_uri = "{redirect_uri}"\n')
    f.write(f'cookie_secret = "{cookie_secret}"\n')
    f.write(f"allowed_domains = {json.dumps(allowed_domains)}\n")
    f.write(f"allowed_emails = {json.dumps(allowed_emails)}\n")
    f.write(f"admin_users = {json.dumps(admin_users)}\n\n")

    f.write("[auth.google]\n")
    f.write(f'client_id = "{client_id}"\n')
    f.write(f'client_secret = "{client_secret}"\n')
    f.write('server_metadata_url = "https://accounts.google.com/.well-known/openid-configuration"\n\n')

    f.write("[auth.domain_org_mapping]\n")
    for domain, org_id in domain_org_mapping.items():
        f.write(f'"{domain}" = "{org_id}"\n')
    f.write("\n")

    f.write("[auth.email_org_mapping]\n")
    for email, org_id in email_org_mapping.items():
        f.write(f'"{email}" = "{org_id}"\n')

print(f"Dynamically generated {secrets_file} from environment variables.")

