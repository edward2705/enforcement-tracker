"""Illumio PCE connection settings (config.yaml) and a small read-only REST client.

The tracker only pulls data. ReadOnlySession enforces that for every request this module can send:
GET is allowed; anything that could change the PCE (POST, PUT, PATCH, DELETE) is refused before it
leaves the machine. The one exception is the Explorer traffic query, which the PCE only offers as a
POST: creating that query, and deleting the query this client created, are allowed and nothing else."""
import datetime
import os
import re
import tempfile
import time
from urllib.parse import urlsplit

import requests
import yaml

CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.yaml")
CONFIG_HEADER = (
    "# One block per PCE; default_pce is the one the app opens with.\n"
    "# The API URL is built as https://<pce_fqdn>:<port>/api/v2/orgs/<org>/...\n"
    "# This file is rewritten by the PCE Configuration page.\n"
)
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,39}$")
SYNC_PAGE_LIMIT = 500  # the PCE caps synchronous GET collections at 500 items


def load_config():
    if not os.path.exists(CONFIG_PATH):
        return {}
    with open(CONFIG_PATH) as fh:
        return yaml.safe_load(fh) or {}


def save_config(cfg):
    """Rewrite config.yaml atomically, keeping it readable only by the owner."""
    ordered = {"default_pce": cfg.get("default_pce")}
    ordered.update({k: v for k, v in cfg.items() if k != "default_pce"})
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(CONFIG_PATH), prefix=".config-")
    try:
        with os.fdopen(fd, "w") as fh:
            fh.write(CONFIG_HEADER)
            yaml.safe_dump(ordered, fh, sort_keys=False, default_flow_style=False)
        os.chmod(tmp, 0o600)
        os.replace(tmp, CONFIG_PATH)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def pce_names(cfg):
    return [k for k, v in cfg.items() if isinstance(v, dict)]


class PCEError(Exception):
    pass


class ReadOnlySession(requests.Session):
    """A requests session that cannot change anything in the PCE.

    Every request goes through request(), so the check also covers code that uses the session
    directly. Allowed: GET to this PCE; POST to the org's traffic query collection (a read that the
    PCE runs as a background query); DELETE of a traffic query this session created. Everything
    else raises PCEError without being sent."""

    def __init__(self, host, org):
        super().__init__()
        self.host = host.lower()                       # "fqdn:port"; credentials are never sent anywhere else
        self.query_path = f"/api/v2/orgs/{org}/traffic_flows/async_queries"
        self.own_queries = set()                       # paths of the traffic queries created by this session

    def request(self, method, url, *args, **kwargs):
        method = (method or "").upper()
        target = urlsplit(url)
        path = target.path.rstrip("/")
        if target.scheme != "https" or target.netloc.lower() != self.host:
            raise PCEError(f"Blocked {method} to {target.netloc or url}: requests only go to the configured PCE over HTTPS")
        allowed = (method == "GET"
                   or (method == "POST" and path == self.query_path)
                   or (method == "DELETE" and path in self.own_queries))
        if not allowed:
            raise PCEError(f"Blocked {method} {target.path}: the tracker only reads from the PCE and never changes it")
        return super().request(method, url, *args, **kwargs)


class PCE:
    def __init__(self, name, conf):
        self.name = name
        self.fqdn = str(conf.get("pce_fqdn") or "").strip()
        self.port = int(conf.get("port") or 443)
        self.org = int(conf.get("org") or 1)
        if not self.fqdn:
            raise PCEError(f"PCE '{name}' has no pce_fqdn configured")
        self.root = f"https://{self.fqdn}:{self.port}/api/v2"
        self.base = f"{self.root}/orgs/{self.org}"
        self.session = ReadOnlySession(f"{self.fqdn}:{self.port}", self.org)
        self.session.auth = (str(conf.get("api_key") or ""), str(conf.get("api_secret") or ""))
        self.session.headers["Accept"] = "application/json"
        cert = conf.get("cert_path")
        if cert:
            if not os.path.exists(cert):
                raise PCEError(f"cert_path not found: {cert}")
            self.session.verify = cert

    @property
    def ui_base(self):
        return f"https://{self.fqdn}:{self.port}"

    def _check(self, resp):
        if resp.status_code in (401, 403):
            raise PCEError(f"PCE rejected the API credentials (HTTP {resp.status_code})")
        if resp.status_code >= 400:
            raise PCEError(f"PCE returned HTTP {resp.status_code} for {resp.request.path_url}")
        return resp

    def get(self, path, params=None, timeout=30):
        return self._check(self.session.get(self.base + path, params=params, timeout=timeout))

    def root_get(self, path, timeout=30):
        """GET a resource that is not under the org, such as /product_version or /health."""
        return self._check(self.session.get(self.root + path, timeout=timeout))

    def collection(self, path, params=None):
        """Return a full collection, switching to an async job past the sync cap."""
        data = self.get(path, params).json()
        if len(data) < SYNC_PAGE_LIMIT:
            return data
        return self.collection_async(path, params)

    def collection_async(self, path, params=None, max_wait=300):
        resp = self._check(
            self.session.get(self.base + path, params=params, timeout=30,
                             headers={"Prefer": "respond-async"})
        )
        job = resp.headers.get("Location")
        if resp.status_code != 202 or not job:
            return resp.json()
        deadline = time.time() + max_wait
        wait = float(resp.headers.get("Retry-After") or 1)
        while True:
            time.sleep(min(max(wait, 0.5), 5))
            status = self._check(self.session.get(self.root + job, timeout=30)).json()
            if status.get("status") == "done":
                return self._check(
                    self.session.get(self.root + status["result"]["href"], timeout=120)
                ).json()
            if status.get("status") == "failed":
                raise PCEError(f"PCE async job failed for {path}")
            if time.time() > deadline:
                raise PCEError(f"Timed out waiting for the PCE async job for {path}")

    def traffic_flows(self, days, decisions, max_results=100000, max_wait=180):
        """Flows with the given policy decisions over the last `days` days, from an Explorer async query.

        This is the one request that is not a GET: the PCE only answers traffic questions through a
        query it runs in the background. It reads traffic and changes no workload, label or policy;
        the query is deleted again once its result is downloaded (see ReadOnlySession)."""
        end = datetime.datetime.now(datetime.timezone.utc)
        body = {
            "query_name": "enforcement-tracker blocked traffic",
            "sources": {"include": [], "exclude": []}, "destinations": {"include": [], "exclude": []},
            "services": {"include": [], "exclude": []}, "sources_destinations_query_op": "and",
            "start_date": (end - datetime.timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "end_date": end.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "policy_decisions": list(decisions), "max_results": max_results,
        }
        job = self._check(self.session.post(self.base + "/traffic_flows/async_queries", json=body, timeout=30)).json()["href"]
        self.session.own_queries.add(urlsplit(self.root + job).path)   # the only thing this client may delete
        try:
            deadline = time.time() + max_wait
            while True:
                status = self._check(self.session.get(self.root + job, timeout=30)).json().get("status")
                if status == "completed":
                    break
                if status in ("failed", "cancelled", "killed"):
                    raise PCEError(f"PCE traffic query {status}")
                if time.time() > deadline:
                    raise PCEError("Timed out waiting for the PCE traffic query")
                time.sleep(2)
            return self._check(self.session.get(self.root + job + "/download", timeout=180)).json()
        finally:
            try:
                self.session.delete(self.root + job, timeout=30)
            except requests.RequestException:
                pass
