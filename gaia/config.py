from __future__ import annotations

import json
import os
from ipaddress import ip_address
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit


SUPPORTED_CONFIG_VERSION = 1


class ConfigError(RuntimeError):
    pass


@dataclass(frozen=True)
class Settings:
    config_version: int
    app_dir: Path
    workspace: Path
    vault: Path
    projects: Path
    service_docs: Path
    run_journal_dir: Path
    safety_audit_dir: Path
    runs_dir: Path
    uploads_dir: Path
    obsidian_work: Path
    transcriber_path: Path
    lm_studio_launcher: Path
    transcriber_launcher: Path
    lm_studio_endpoint: str
    local_llm_prompt_char_limit: int
    local_llm_max_tokens: int
    local_llm_default_provider: str
    local_llm_providers: dict[str, dict[str, Any]]
    local_llm_routes: dict[str, dict[str, Any]]
    host: str
    port: int
    retention_runs_days: int
    retention_journals_days: int
    retention_audit_days: int
    retention_cleanup_on_startup: bool
    lore_semantic_rerank: bool
    lore_rerank_candidates: int
    lore_rerank_timeout_seconds: int
    lore_query_rewrite: bool
    lore_query_rewrite_timeout_seconds: int
    lore_gap_detector: bool
    lore_gap_detector_timeout_seconds: int
    veil_llm_review: bool
    veil_llm_review_timeout_seconds: int
    scribe_candidate_classifier: bool
    scribe_classifier_timeout_seconds: int
    project_health_llm: bool
    project_health_timeout_seconds: int
    analyze_job_timeout_seconds: int
    transcription_timeout_seconds: int
    completed_job_retention_seconds: int
    config_path: Path


def default_config_path(app_dir: Path) -> Path:
    return Path(os.environ.get("GAIA_CONFIG", app_dir / "config.json")).expanduser()


def read_config(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise ConfigError(f"Config file not found: {path}")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ConfigError(f"Config JSON is invalid at {path}: {exc}") from exc


def require_mapping(payload: dict[str, Any], key: str) -> dict[str, Any]:
    value = payload.get(key)
    if not isinstance(value, dict):
        raise ConfigError(f"Config section `{key}` must be an object.")
    return value


def require_str(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"Config value `{key}` must be a non-empty string.")
    return value


def optional_int(payload: dict[str, Any], key: str, default: int) -> int:
    value = payload.get(key, default)
    if not isinstance(value, int) or value < 0:
        raise ConfigError(f"Config value `{key}` must be a non-negative integer.")
    return value


def optional_positive_int(payload: dict[str, Any], key: str, default: int) -> int:
    value = payload.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ConfigError(f"Config value `{key}` must be a positive integer.")
    return value


def optional_bool(payload: dict[str, Any], key: str, default: bool) -> bool:
    value = payload.get(key, default)
    if not isinstance(value, bool):
        raise ConfigError(f"Config value `{key}` must be true or false.")
    return value


def optional_mapping(payload: dict[str, Any], key: str) -> dict[str, Any]:
    value = payload.get(key, {})
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ConfigError(f"Config value `{key}` must be an object.")
    return value


def config_version(payload: dict[str, Any]) -> int:
    value = payload.get("config_version", SUPPORTED_CONFIG_VERSION)
    if not isinstance(value, int):
        raise ConfigError("Config value `config_version` must be an integer.")
    if value != SUPPORTED_CONFIG_VERSION:
        raise ConfigError(
            f"Unsupported config_version {value}; supported version is {SUPPORTED_CONFIG_VERSION}."
        )
    return value


def expand_value(value: str, tokens: dict[str, str]) -> str:
    result = os.path.expanduser(value)
    for key, token_value in tokens.items():
        result = result.replace("${" + key + "}", token_value)
    return os.path.expandvars(result)


def expand_path(value: str, tokens: dict[str, str]) -> Path:
    return Path(expand_value(value, tokens)).expanduser()


def validate_local_endpoint(endpoint: str, key: str) -> None:
    parts = urlsplit(endpoint)
    if parts.scheme not in {"http", "https"} or not parts.hostname:
        raise ConfigError(f"Config value `{key}` must be an absolute HTTP(S) URL on a loopback address.")
    try:
        is_loopback = ip_address(parts.hostname).is_loopback
    except ValueError:
        is_loopback = False
    if not is_loopback:
        raise ConfigError(f"Config value `{key}` must use a loopback IP address; remote LLM endpoints are forbidden.")


def validate_loopback_host(host: str, key: str) -> None:
    try:
        is_loopback = ip_address(host).is_loopback
    except ValueError:
        is_loopback = False
    if not is_loopback:
        raise ConfigError(f"Config value `{key}` must use a loopback IP address.")


def load_local_llm_providers(local_llm: dict[str, Any], lm_studio_endpoint: str, tokens: dict[str, str]) -> dict[str, dict[str, Any]]:
    raw = optional_mapping(local_llm, "providers")
    if not raw:
        raw = {
            "lm_studio": {
                "type": "openai_compatible",
                "endpoint": lm_studio_endpoint,
                "model": "local-model",
                "enabled": True,
            }
        }
    providers: dict[str, dict[str, Any]] = {}
    for name, value in raw.items():
        if not isinstance(name, str) or not name.strip():
            raise ConfigError("Config value `local_llm.providers` must use non-empty provider names.")
        if not isinstance(value, dict):
            raise ConfigError(f"Config value `local_llm.providers.{name}` must be an object.")
        provider_type = str(value.get("type") or "openai_compatible").strip()
        if provider_type not in {"openai_compatible", "ollama"}:
            raise ConfigError(
                f"Config value `local_llm.providers.{name}.type` must be `openai_compatible` or `ollama`."
            )
        endpoint = value.get("endpoint")
        if not isinstance(endpoint, str) or not endpoint.strip():
            raise ConfigError(f"Config value `local_llm.providers.{name}.endpoint` must be a non-empty string.")
        model = value.get("model")
        if not isinstance(model, str) or not model.strip():
            raise ConfigError(f"Config value `local_llm.providers.{name}.model` must be a non-empty string.")
        enabled = value.get("enabled", True)
        if not isinstance(enabled, bool):
            raise ConfigError(f"Config value `local_llm.providers.{name}.enabled` must be true or false.")
        expanded_endpoint = expand_value(endpoint, tokens)
        validate_local_endpoint(expanded_endpoint, f"local_llm.providers.{name}.endpoint")
        provider: dict[str, Any] = {
            "type": provider_type,
            "endpoint": expanded_endpoint,
            "model": model.strip(),
            "enabled": enabled,
        }
        if provider_type == "ollama":
            for key, default in (("thinking", False), ("json_mode", True)):
                option_value = value.get(key, default)
                if not isinstance(option_value, bool):
                    raise ConfigError(f"Config value `local_llm.providers.{name}.{key}` must be true or false.")
                provider[key] = option_value
            context_length = value.get("context_length")
            if context_length is not None:
                if isinstance(context_length, bool) or not isinstance(context_length, int) or context_length <= 0:
                    raise ConfigError(f"Config value `local_llm.providers.{name}.context_length` must be a positive integer.")
                provider["context_length"] = context_length
        providers[name.strip()] = provider
    return providers


def load_local_llm_routes(local_llm: dict[str, Any], providers: dict[str, dict[str, Any]], default_provider: str) -> dict[str, dict[str, Any]]:
    raw = optional_mapping(local_llm, "routes")
    routes: dict[str, dict[str, Any]] = {}
    for name, value in raw.items():
        if not isinstance(name, str) or not name.strip():
            raise ConfigError("Config value `local_llm.routes` must use non-empty route names.")
        if isinstance(value, str):
            value = {"provider": value}
        if not isinstance(value, dict):
            raise ConfigError(f"Config value `local_llm.routes.{name}` must be an object or provider name.")
        provider = value.get("provider", default_provider)
        if not isinstance(provider, str) or provider not in providers:
            raise ConfigError(f"Config value `local_llm.routes.{name}.provider` must reference a configured provider.")
        route: dict[str, Any] = {"provider": provider}
        model = value.get("model")
        if model is not None:
            if not isinstance(model, str) or not model.strip():
                raise ConfigError(f"Config value `local_llm.routes.{name}.model` must be a non-empty string.")
            route["model"] = model.strip()
        for key in ("prompt_char_limit", "max_tokens"):
            option_value = value.get(key)
            if option_value is None:
                continue
            if isinstance(option_value, bool) or not isinstance(option_value, int) or option_value <= 0:
                raise ConfigError(f"Config value `local_llm.routes.{name}.{key}` must be a positive integer.")
            route[key] = option_value
        routes[name.strip()] = route
    return routes


def load_settings(validate: bool = True) -> Settings:
    app_dir = Path(__file__).resolve().parents[1]
    workspace = app_dir.parent
    config_path = default_config_path(app_dir)
    payload = read_config(config_path)
    version = config_version(payload)
    paths = require_mapping(payload, "paths")
    server = require_mapping(payload, "server")
    endpoints = require_mapping(payload, "endpoints")
    retention = payload.get("retention", {})
    if not isinstance(retention, dict):
        raise ConfigError("Config section `retention` must be an object.")
    lore = payload.get("lore", {})
    if not isinstance(lore, dict):
        raise ConfigError("Config section `lore` must be an object.")
    local_llm = payload.get("local_llm", {})
    if not isinstance(local_llm, dict):
        raise ConfigError("Config section `local_llm` must be an object.")
    processing = payload.get("processing", {})
    if not isinstance(processing, dict):
        raise ConfigError("Config section `processing` must be an object.")

    base_tokens = {
        "HOME": str(Path.home()),
        "APP_DIR": str(app_dir),
        "WORKSPACE": str(workspace),
    }
    vault = expand_path(require_str(paths, "vault"), base_tokens)
    tokens = dict(base_tokens)
    tokens["VAULT"] = str(vault)

    projects = expand_path(require_str(paths, "projects"), tokens)
    service_docs = expand_path(require_str(paths, "service_docs"), tokens)
    runs_dir = expand_path(require_str(paths, "runs"), tokens)
    obsidian_work = expand_path(require_str(paths, "obsidian_work"), tokens)
    transcriber = expand_path(require_str(paths, "transcriber"), tokens)
    lm_studio_launcher = expand_path(require_str(paths, "lm_studio_launcher"), tokens)
    transcriber_launcher = expand_path(require_str(paths, "transcriber_launcher"), tokens)
    host = require_str(server, "host")
    validate_loopback_host(host, "server.host")
    port_value = server.get("port")
    if not isinstance(port_value, int) or not 1 <= port_value <= 65535:
        raise ConfigError("Config value `server.port` must be an integer from 1 to 65535.")
    lm_studio_endpoint = require_str(endpoints, "lm_studio")
    validate_local_endpoint(lm_studio_endpoint, "endpoints.lm_studio")
    local_llm_providers = load_local_llm_providers(local_llm, lm_studio_endpoint, tokens)
    default_provider = local_llm.get("default_provider", "lm_studio" if "lm_studio" in local_llm_providers else next(iter(local_llm_providers)))
    if not isinstance(default_provider, str) or default_provider not in local_llm_providers:
        raise ConfigError("Config value `local_llm.default_provider` must reference a configured provider.")
    local_llm_routes = load_local_llm_routes(local_llm, local_llm_providers, default_provider)

    settings = Settings(
        config_version=version,
        app_dir=app_dir,
        workspace=workspace,
        vault=vault,
        projects=projects,
        service_docs=service_docs,
        run_journal_dir=service_docs / "Журнал запросов",
        safety_audit_dir=service_docs / "Аудит безопасности",
        runs_dir=runs_dir,
        uploads_dir=runs_dir / "uploads",
        obsidian_work=obsidian_work,
        transcriber_path=transcriber,
        lm_studio_launcher=lm_studio_launcher,
        transcriber_launcher=transcriber_launcher,
        lm_studio_endpoint=lm_studio_endpoint,
        local_llm_prompt_char_limit=optional_int(local_llm, "prompt_char_limit", 16000),
        local_llm_max_tokens=optional_int(local_llm, "max_tokens", 1200),
        local_llm_default_provider=default_provider,
        local_llm_providers=local_llm_providers,
        local_llm_routes=local_llm_routes,
        host=host,
        port=port_value,
        retention_runs_days=optional_int(retention, "runs_days", 7),
        retention_journals_days=optional_int(retention, "journals_days", 30),
        retention_audit_days=optional_int(retention, "audit_days", 365),
        retention_cleanup_on_startup=optional_bool(retention, "cleanup_on_startup", False),
        lore_semantic_rerank=optional_bool(lore, "semantic_rerank", False),
        lore_rerank_candidates=optional_int(lore, "rerank_candidates", 24),
        lore_rerank_timeout_seconds=optional_int(lore, "rerank_timeout_seconds", 45),
        lore_query_rewrite=optional_bool(lore, "query_rewrite", False),
        lore_query_rewrite_timeout_seconds=optional_int(lore, "query_rewrite_timeout_seconds", 4),
        lore_gap_detector=optional_bool(lore, "gap_detector", False),
        lore_gap_detector_timeout_seconds=optional_int(lore, "gap_detector_timeout_seconds", 4),
        veil_llm_review=optional_bool(lore, "veil_llm_review", False),
        veil_llm_review_timeout_seconds=optional_int(lore, "veil_llm_review_timeout_seconds", 4),
        scribe_candidate_classifier=optional_bool(lore, "scribe_candidate_classifier", False),
        scribe_classifier_timeout_seconds=optional_int(lore, "scribe_classifier_timeout_seconds", 5),
        project_health_llm=optional_bool(lore, "project_health_llm", False),
        project_health_timeout_seconds=optional_int(lore, "project_health_timeout_seconds", 5),
        analyze_job_timeout_seconds=optional_positive_int(processing, "analyze_job_timeout_seconds", 900),
        transcription_timeout_seconds=optional_positive_int(processing, "transcription_timeout_seconds", 600),
        completed_job_retention_seconds=optional_positive_int(processing, "completed_job_retention_seconds", 1800),
        config_path=config_path,
    )
    if validate:
        validate_settings(settings)
    return settings


def validate_settings(settings: Settings) -> None:
    if not settings.vault.exists():
        raise ConfigError(f"Vault path does not exist: {settings.vault}")
    if not settings.projects.exists():
        raise ConfigError(f"Projects path does not exist: {settings.projects}")
    if settings.service_docs.is_relative_to(settings.projects):
        raise ConfigError("Service docs path must be outside the projects path.")
    if not settings.obsidian_work.exists():
        raise ConfigError(f"Obsidian work path does not exist: {settings.obsidian_work}")


_SETTINGS_ERROR: ConfigError | None = None
try:
    SETTINGS: Settings | None = load_settings(validate=False)
except ConfigError as exc:
    SETTINGS = None
    _SETTINGS_ERROR = exc


MEDIA_EXTENSIONS = {
    ".mp3", ".mp4", ".mpeg", ".mpga", ".m4a", ".wav", ".webm", ".flac",
    ".ogg", ".aac", ".aiff", ".aif", ".mov", ".mkv", ".avi", ".m4v", ".mpg",
    ".3gp",
}
DOCUMENT_EXTENSIONS = {".txt", ".md", ".pdf", ".docx", ".xlsx"}
SUPPORTED_EXTENSIONS = DOCUMENT_EXTENSIONS | MEDIA_EXTENSIONS


def ensure_dirs() -> None:
    if _SETTINGS_ERROR:
        raise _SETTINGS_ERROR
    if SETTINGS is None:
        raise ConfigError("Settings are unavailable.")
    validate_settings(SETTINGS)
    SETTINGS.runs_dir.mkdir(parents=True, exist_ok=True)
    SETTINGS.uploads_dir.mkdir(parents=True, exist_ok=True)
    SETTINGS.service_docs.mkdir(parents=True, exist_ok=True)
    (SETTINGS.vault / "Контексты" / "Группы").mkdir(parents=True, exist_ok=True)
    SETTINGS.run_journal_dir.mkdir(parents=True, exist_ok=True)
    SETTINGS.safety_audit_dir.mkdir(parents=True, exist_ok=True)


def describe_settings(settings: Settings | None = SETTINGS) -> str:
    if settings is None:
        raise ConfigError("Settings are unavailable.")
    return "\n".join([
        f"config: {settings.config_path}",
        f"config_version: {settings.config_version}",
        f"url: http://{settings.host}:{settings.port}",
        f"vault: {settings.vault}",
        f"projects: {settings.projects}",
        f"service_docs: {settings.service_docs}",
        f"runs: {settings.runs_dir}",
        f"retention: runs={settings.retention_runs_days}d journals={settings.retention_journals_days}d audit={settings.retention_audit_days}d cleanup_on_startup={settings.retention_cleanup_on_startup}",
        f"lore: semantic_rerank={settings.lore_semantic_rerank} candidates={settings.lore_rerank_candidates} timeout={settings.lore_rerank_timeout_seconds}s query_rewrite={settings.lore_query_rewrite} gap_detector={settings.lore_gap_detector} veil_llm_review={settings.veil_llm_review} scribe_classifier={settings.scribe_candidate_classifier} project_health_llm={settings.project_health_llm}",
        f"processing: analyze_job_timeout={settings.analyze_job_timeout_seconds}s transcription_timeout={settings.transcription_timeout_seconds}s completed_job_retention={settings.completed_job_retention_seconds}s",
        f"lm_studio_endpoint: {settings.lm_studio_endpoint}",
        f"local_llm: prompt_char_limit={settings.local_llm_prompt_char_limit} max_tokens={settings.local_llm_max_tokens} default_provider={settings.local_llm_default_provider} providers={','.join(settings.local_llm_providers)} routes={','.join(settings.local_llm_routes)}",
    ])


def main() -> int:
    try:
        if _SETTINGS_ERROR:
            raise _SETTINGS_ERROR
        if SETTINGS is None:
            raise ConfigError("Settings are unavailable.")
        validate_settings(SETTINGS)
        ensure_dirs()
        print("Gaia config OK")
        print(describe_settings())
        return 0
    except ConfigError as exc:
        print(f"Gaia config error: {exc}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
