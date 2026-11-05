import requests

from core import config

REQUEST_TIMEOUT = 30


def github_credentials():
    """(repo, headers) per le API di GitHub, oppure None se il token non è configurato."""
    if not (config.ASTRO_GITHUB_REPO and config.ASTRO_GITHUB_TOKEN):
        return None
    headers = {
        "Authorization": f"Bearer {config.ASTRO_GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    return config.ASTRO_GITHUB_REPO, headers


def gh_request(method, path, **kwargs):
    """Chiama https://api.github.com/repos/<repo>/<path>; solleva RuntimeError se GitHub non è configurato."""
    creds = github_credentials()
    if not creds:
        raise RuntimeError("Credenziali GitHub non configurate")
    repo, headers = creds
    return requests.request(
        method, f"https://api.github.com/repos/{repo}/{path}",
        headers=headers, timeout=REQUEST_TIMEOUT, **kwargs,
    )


def r2_client():
    """Client S3 (boto3) per il bucket R2, oppure None se R2 non è configurato del tutto."""
    if not (config.R2_ACCOUNT_ID and config.R2_ACCESS_KEY_ID and config.R2_SECRET_ACCESS_KEY
            and config.R2_BUCKET and config.R2_PUBLIC_URL):
        return None
    import boto3  # importato solo qui: serve soltanto per l'upload

    return boto3.client(
        "s3",
        endpoint_url=f"https://{config.R2_ACCOUNT_ID}.r2.cloudflarestorage.com",
        region_name="auto",
        aws_access_key_id=config.R2_ACCESS_KEY_ID,
        aws_secret_access_key=config.R2_SECRET_ACCESS_KEY,
    )
