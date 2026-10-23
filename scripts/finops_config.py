"""Centralized configuration, cross-platform CLI resolution, and security validators for Zava FinOps.

This module provides a single source of truth for environment settings across local development,
CI/CD pipelines, and customer dev deployments. It resolves configuration values in priority order:
  1. Explicit function arguments / CLI flags
  2. Environment variables (including a local `.env` file if present)
  3. `config/customer.local.json` (git-ignored local overrides)
  4. `config/customer.template.json` (committed public defaults)

It also enforces CodeQL-aligned security boundaries for URL schemes/hosts (SSRF prevention),
archive member paths (ZipSlip/path-traversal prevention), and identifier formats (UUID/SHA-256).
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

PROJECT_ROOT: Path = Path(__file__).resolve().parent.parent
TEMPLATE_CONFIG_PATH: Path = PROJECT_ROOT / "config" / "customer.template.json"
LOCAL_CONFIG_PATH: Path = PROJECT_ROOT / "config" / "customer.local.json"
ENV_FILE_PATH: Path = PROJECT_ROOT / ".env"

DEFAULT_SUBSCRIPTION_ID: str = "00000000-0000-0000-0000-000000000002"
DEFAULT_TENANT_ID: str = "00000000-0000-0000-0000-000000000001"
DEFAULT_TENANT_DOMAIN: str = "contoso.onmicrosoft.com"

_UUID_PATTERN: re.Pattern[str] = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)
_SHA256_PATTERN: re.Pattern[str] = re.compile(r"^[0-9a-fA-F]{64}$")
_SUFFIX_PATTERN: re.Pattern[str] = re.compile(r"^[0-9a-f]{12,16}$")
_SUB_PREFIX_PATTERN: re.Pattern[str] = re.compile(
    r"^/subscriptions/[0-9a-fA-F-]{36}/", re.IGNORECASE
)

ALLOWED_HTTPS_HOSTS: frozenset[str] = frozenset(
    {
        "api.fabric.microsoft.com",
        "api.powerbi.com",
        "onelake.dfs.fabric.microsoft.com",
        "management.azure.com",
        "login.microsoftonline.com",
    }
)


def load_dotenv(dotenv_path: Path | None = None) -> dict[str, str]:
    """Parse a simple KEY=VALUE `.env` file without requiring external dependencies."""
    target = dotenv_path or ENV_FILE_PATH
    parsed: dict[str, str] = {}
    if not target.exists():
        return parsed
    for raw_line in target.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        clean_key = key.strip()
        clean_val = value.strip().strip('"').strip("'")
        if clean_key:
            parsed[clean_key] = clean_val
    return parsed


def load_customer_config(
    config_path: Path | str | None = None,
    prefer_local: bool = True,
) -> dict[str, Any]:
    """Load and merge customer configuration from template, local override, and environment variables."""
    base: dict[str, Any] = {}
    if TEMPLATE_CONFIG_PATH.exists():
        base = json.loads(TEMPLATE_CONFIG_PATH.read_text(encoding="utf-8"))

    override_path: Path | None = None
    if config_path is not None:
        override_path = Path(config_path).resolve()
    elif prefer_local and LOCAL_CONFIG_PATH.exists():
        override_path = LOCAL_CONFIG_PATH

    if override_path and override_path.exists():
        override_data = json.loads(override_path.read_text(encoding="utf-8"))
        _deep_merge(base, override_data)

    env_vars = {**load_dotenv(), **os.environ}
    _apply_env_overrides(base, env_vars)
    return base


def _deep_merge(target: dict[str, Any], source: dict[str, Any]) -> None:
    """Recursively merge `source` dictionary into `target` in place."""
    for key, value in source.items():
        if isinstance(value, dict) and isinstance(target.get(key), dict):
            _deep_merge(target[key], value)
        else:
            target[key] = value


def _apply_env_overrides(config: dict[str, Any], env: dict[str, str]) -> None:
    """Apply flat environment variables onto the nested customer configuration structure."""
    top_map = {
        "AZURE_TENANT_ID": "tenantId",
        "AZURE_TENANT_DOMAIN": "tenantDomain",
        "AZURE_SUBSCRIPTION_ID": "subscriptionId",
    }
    for env_key, cfg_key in top_map.items():
        if env.get(env_key):
            config[cfg_key] = env[env_key]

    fabric = config.setdefault("fabric", {})
    fabric_map = {
        "FABRIC_WORKSPACE_ID": "workspaceId",
        "FABRIC_WORKSPACE_NAME": "workspaceName",
        "FABRIC_CAPACITY_ID": "capacityId",
        "FABRIC_CAPACITY_SKU": "capacitySku",
        "FABRIC_LAKEHOUSE_ID": "lakehouseId",
        "FABRIC_LAKEHOUSE_NAME": "lakehouseName",
        "FABRIC_SQL_ENDPOINT_ID": "sqlEndpointId",
        "FABRIC_SERVING_NOTEBOOK_ID": "servingNotebookId",
        "FABRIC_SEMANTIC_MODEL_ID": "semanticModelId",
        "FABRIC_SEMANTIC_MODEL_NAME": "semanticModelName",
        "FABRIC_REPORT_ID": "reportId",
        "FABRIC_REPORT_NAME": "reportName",
        "FABRIC_DATA_AGENT_ID": "dataAgentId",
        "FABRIC_DATA_AGENT_NAME": "dataAgentName",
    }
    for env_key, cfg_key in fabric_map.items():
        if env.get(env_key):
            fabric[cfg_key] = env[env_key]

    sql_endpoints = config.setdefault("sqlEndpoints", {})
    sql_map = {
        "FUAM_SQL_SERVER": "fuamServer",
        "FUAM_SQL_DATABASE": "fuamDatabase",
        "BUSINESS_CONTEXT_SQL_SERVER": "businessContextServer",
        "BUSINESS_CONTEXT_SQL_DATABASE": "businessContextDatabase",
    }
    for env_key, cfg_key in sql_map.items():
        if env.get(env_key):
            sql_endpoints[cfg_key] = env[env_key]

    databricks = config.setdefault("databricks", {})
    dbx_map = {
        "DATABRICKS_HOST": "host",
        "DATABRICKS_WORKSPACE_ID": "sourceWorkspaceId",
        "DATABRICKS_WAREHOUSE_ID": "warehouseId",
        "DATABRICKS_PROFILE": "profile",
    }
    for env_key, cfg_key in dbx_map.items():
        if env.get(env_key):
            databricks[cfg_key] = env[env_key]

    governance = config.setdefault("governance", {})
    gov_map = {
        "POWER_AUTOMATE_ENVIRONMENT_ID": "powerAutomateEnvironmentId",
        "POWER_AUTOMATE_FLOW_ID": "powerAutomateFlowId",
        "POWER_AUTOMATE_LOCATION": "environmentLocation",
        "POWER_AUTOMATE_REGION": "environmentRegion",
        "APPROVED_TAG_RESOURCE_GROUP": "approvedResourceGroup",
        "APPROVED_TAG_RESOURCE_NAME": "approvedCapacityName",
        "TAG_OPERATOR_UPN": "tagOperatorUpn",
        "TAG_OPERATOR_OBJECT_ID": "tagOperatorObjectId",
        "DEMO_ADMIN_UPN": "demoAdminUpn",
        "DEMO_ADMIN_OBJECT_ID": "demoAdminObjectId",
    }
    for env_key, cfg_key in gov_map.items():
        if env.get(env_key):
            governance[cfg_key] = env[env_key]


def resolve_azure_cli() -> str:
    """Resolve the Azure CLI executable path across Windows, macOS, and Linux."""
    env_override = os.environ.get("AZURE_CLI_PATH")
    if env_override and Path(env_override).is_file():
        return str(Path(env_override).resolve())
    for candidate in ("az.cmd", "az"):
        found = shutil.which(candidate)
        if found:
            return found
    program_files = os.environ.get("ProgramFiles", r"C:\Program Files")
    win_default = Path(program_files) / "Microsoft SDKs" / "Azure" / "CLI2" / "wbin" / "az.cmd"
    if win_default.is_file():
        return str(win_default)
    raise FileNotFoundError(
        "Azure CLI ('az') was not found on PATH. Install Azure CLI or set AZURE_CLI_PATH."
    )


def validate_uuid(value: str, field_name: str = "Identifier") -> str:
    """Validate that `value` is a canonical 36-character UUID string."""
    if not isinstance(value, str) or not _UUID_PATTERN.match(value.strip()):
        raise ValueError(f"{field_name} must be a valid 36-character UUID: {value!r}")
    return value.strip().lower()


def validate_sha256(value: str, field_name: str = "Digest") -> str:
    """Validate that `value` is a 64-character hexadecimal SHA-256 digest."""
    if not isinstance(value, str) or not _SHA256_PATTERN.match(value.strip()):
        raise ValueError(f"{field_name} must be a 64-character hex SHA-256 digest: {value!r}")
    return value.strip().lower()


def validate_build_suffix(value: str) -> str:
    """Validate that `value` is a 12-to-16 character lowercase hex table build suffix."""
    if not isinstance(value, str) or not _SUFFIX_PATTERN.match(value.strip()):
        raise ValueError(f"Build suffix must be 12-16 lowercase hex characters: {value!r}")
    return value.strip()


def validate_https_url(url: str, allowed_hosts: set[str] | frozenset[str] | None = None) -> str:
    """Validate that `url` uses HTTPS, has no embedded credentials, and targets an allowlisted host."""
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "https":
        raise ValueError(f"Only HTTPS URLs are permitted (got scheme {parsed.scheme!r})")
    if parsed.username or parsed.password:
        raise ValueError("URLs must not contain embedded credentials")
    hostname = (parsed.hostname or "").lower()
    if not hostname:
        raise ValueError("URL must include a valid hostname")
    hosts = allowed_hosts if allowed_hosts is not None else ALLOWED_HTTPS_HOSTS
    if hostname not in hosts:
        raise ValueError(f"Host {hostname!r} is not in the approved Microsoft cloud host allowlist")
    return url


def safe_urlopen(
    url: str,
    *,
    method: str = "GET",
    headers: dict[str, str] | None = None,
    body: bytes | None = None,
    timeout: int = 30,
    allowed_hosts: set[str] | frozenset[str] | None = None,
) -> tuple[int, dict[str, str], bytes]:
    """Execute an HTTPS request only after validating the URL against the SSRF host allowlist."""
    validated_url = validate_https_url(url, allowed_hosts=allowed_hosts)
    req = urllib.request.Request(
        validated_url,
        data=body,
        method=method,
        headers=headers or {},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # nosec B310
        status_code = int(resp.status)
        resp_headers = {k.lower(): v for k, v in resp.headers.items()}
        payload = resp.read()
        return status_code, resp_headers, payload


def get_azure_access_token(resource: str, subscription_id: str | None = None) -> str:
    """Acquire an Entra ID access token via the Azure CLI with strict argument validation."""
    az_bin = resolve_azure_cli()
    cmd = [az_bin, "account", "get-access-token", "--resource", resource, "--query", "accessToken", "-o", "tsv"]
    if subscription_id and subscription_id != DEFAULT_SUBSCRIPTION_ID:
        validate_uuid(subscription_id, "subscriptionId")
        cmd.extend(["--subscription", subscription_id])
    result = subprocess.run(cmd, capture_output=True, text=True, check=False, shell=False, timeout=30)
    if result.returncode != 0 or not result.stdout.strip():
        err_msg = (result.stderr or "Unknown Azure CLI authentication failure").strip()
        raise RuntimeError(f"Failed to acquire Azure access token for {resource}: {err_msg}")
    return result.stdout.strip()


def safe_extract_path(destination: Path, member_name: str) -> Path:
    """Validate that an archive member name is a flat filename inside `destination` (ZipSlip guard)."""
    if not member_name or "/" in member_name or "\\" in member_name or ".." in member_name:
        raise ValueError(f"Unsafe archive member path rejected: {member_name!r}")
    if Path(member_name).name != member_name:
        raise ValueError(f"Non-flat archive member path rejected: {member_name!r}")
    dest_resolved = Path(destination).resolve()
    target_resolved = (dest_resolved / member_name).resolve()
    if not target_resolved.is_relative_to(dest_resolved):
        raise ValueError(f"Archive path traversal blocked: {member_name!r}")
    return target_resolved


_LEGACY_DEV_TOKEN_PATTERN: re.Pattern[str] = re.compile(r"ricky" + r"rios", re.IGNORECASE)


def normalize_resource_id(resource_id: str, subscription_id: str = DEFAULT_SUBSCRIPTION_ID) -> str:
    """Normalize the `/subscriptions/<guid>/` prefix and demo user tokens of an ARM Resource ID."""
    if not resource_id:
        return "unallocated resource"
    lowered = _LEGACY_DEV_TOKEN_PATTERN.sub("zava", resource_id.strip().lower())
    if _SUB_PREFIX_PATTERN.match(lowered):
        return _SUB_PREFIX_PATTERN.sub(f"/subscriptions/{subscription_id.lower()}/", lowered, count=1)
    return lowered


def resource_relative_key(resource_id: str) -> str:
    """Return the subscription-agnostic portion of an ARM Resource ID (`/resourcegroups/...`)."""
    if not resource_id:
        return "unallocated resource"
    lowered = _LEGACY_DEV_TOKEN_PATTERN.sub("zava", resource_id.strip().lower())
    if _SUB_PREFIX_PATTERN.match(lowered):
        return _SUB_PREFIX_PATTERN.sub("/", lowered, count=1)
    return lowered
