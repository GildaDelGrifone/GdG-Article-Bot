import base64
import logging
from urllib.parse import quote

from core.site.client import gh_request

logger = logging.getLogger(__name__)


class GitHubError(Exception):
    """Errore di GitHub che non va ignorato (es. impossibile elencare una cartella)."""


def get_ref_sha(branch):
    response = gh_request("GET", f"git/ref/heads/{quote(branch, safe='/')}")
    if response.status_code != 200:
        logger.error("GitHub: impossibile leggere il branch '%s': %s - %s", branch, response.status_code, response.text)
        return None
    return response.json()["object"]["sha"]


def branch_exists(name):
    """True se il branch esiste (nessun log di errore se manca)."""
    response = gh_request("GET", f"git/ref/heads/{quote(name, safe='/')}")
    return response.status_code == 200


def create_branch(name, from_branch):
    """Crea `name` a partire da `from_branch`; un branch già esistente viene riusato."""
    sha = get_ref_sha(from_branch)
    if not sha:
        return False
    response = gh_request("POST", "git/refs", json={"ref": f"refs/heads/{name}", "sha": sha})
    if response.status_code == 201:
        return True
    if response.status_code == 422 and "already exists" in response.text:
        logger.info("GitHub: il branch '%s' esiste già, lo riuso.", name)
        return True
    logger.error("GitHub: creazione del branch '%s' fallita: %s - %s", name, response.status_code, response.text)
    return False


def list_dir(path, branch):
    """I nomi dei file in `path` su `branch`; una cartella inesistente dà []. Altri errori: GitHubError."""
    response = gh_request("GET", f"contents/{quote(path)}", params={"ref": branch})
    if response.status_code == 404:
        return []
    if response.status_code != 200:
        raise GitHubError(f"Impossibile elencare '{path}': {response.status_code}")
    return [entry["name"] for entry in response.json() if entry.get("type") == "file"]


def get_file(path, branch):
    """Lo sha del blob di `path` su `branch`, o None se il file non esiste."""
    response = gh_request("GET", f"contents/{quote(path)}", params={"ref": branch})
    if response.status_code == 200:
        return response.json().get("sha")
    return None


def get_file_text(path, branch):
    """Il testo di `path` su `branch`, o None se non esiste."""
    response = gh_request("GET", f"contents/{quote(path)}", params={"ref": branch})
    if response.status_code != 200:
        return None
    return base64.b64decode(response.json().get("content", "")).decode("utf-8", "replace")


def put_file(path, content, message, branch):
    """Crea o aggiorna `path` su `branch` con il testo `content`."""
    payload = {
        "message": message,
        "content": base64.b64encode(content.encode("utf-8")).decode("ascii"),
        "branch": branch,
    }
    sha = get_file(path, branch)
    if sha:
        payload["sha"] = sha
    response = gh_request("PUT", f"contents/{quote(path)}", json=payload)
    if response.status_code not in (200, 201):
        logger.error("GitHub: commit di '%s' su '%s' fallito: %s - %s", path, branch, response.status_code, response.text)
        return False
    return True


def merge_branch(head, base, message):
    """Fa il merge di `head` in `base`; 201 (unito) e 204 (niente da unire) sono entrambi successo."""
    response = gh_request("POST", "merges", json={"base": base, "head": head, "commit_message": message})
    if response.status_code in (201, 204):
        return True
    logger.error("GitHub: merge di '%s' in '%s' fallito: %s - %s", head, base, response.status_code, response.text)
    return False


def delete_branch(name):
    response = gh_request("DELETE", f"git/refs/heads/{quote(name, safe='/')}")
    if response.status_code != 204:
        logger.warning("GitHub: eliminazione del branch '%s' fallita: %s - %s", name, response.status_code, response.text)
        return False
    return True
