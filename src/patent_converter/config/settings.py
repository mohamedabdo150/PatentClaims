"""Application settings.

Settings are loaded from a YAML file (``PATENT_CONFIG_FILE``, default
``config/app.yaml``) and can be overridden by environment variables using the
``PATENT__<SECTION>__<KEY>`` convention, e.g. ``PATENT__PROCESSING__MAX_PROCESSING_SECONDS=900``.

Every limit used anywhere in the service is defined here: there are no hidden
hard-coded processing limits.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, field_validator, model_validator
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_CONFIG_FILE = PROJECT_ROOT / "config" / "app.yaml"
MB = 1024 * 1024


class ProcessingSettings(BaseModel):
    max_english_file_size_mb: float = 100
    max_arabic_file_size_mb: float = 100
    max_combined_upload_mb: float = 200
    max_claims_per_document: int = 500
    max_processing_seconds: int = 1800
    max_concurrent_jobs_per_worker: int = 1
    max_worker_memory_mb: int = 40960
    upload_chunk_size_kb: int = 1024

    @property
    def max_english_bytes(self) -> int:
        return int(self.max_english_file_size_mb * MB)

    @property
    def max_arabic_bytes(self) -> int:
        return int(self.max_arabic_file_size_mb * MB)

    @property
    def max_combined_bytes(self) -> int:
        return int(self.max_combined_upload_mb * MB)


class StorageSettings(BaseModel):
    temporary_directory: Path = Path("/data/patent-processing")
    max_temporary_storage_gb: float = 800
    min_free_disk_gb: float = 20
    cleanup_after_completion: bool = True
    stale_job_retention_minutes: int = 120


class OutputSettings(BaseModel):
    max_response_size_mb: float = 100
    include_diagnostics: bool = False
    embed_images_as_base64: bool = True
    allow_external_images: bool = False

    @model_validator(mode="after")
    def _enforce_business_rules(self) -> "OutputSettings":
        # Business rules 8-10: assets must always be embedded, never external.
        if not self.embed_images_as_base64:
            raise ValueError("output.embed_images_as_base64 must be true (business rule)")
        if self.allow_external_images:
            raise ValueError("output.allow_external_images must be false (business rule)")
        return self


class SecuritySettings(BaseModel):
    max_zip_entries: int = 10000
    max_uncompressed_size_mb: float = 1024
    max_compression_ratio: float = 150.0
    max_entry_compression_ratio: float = 500.0
    compression_ratio_min_entry_kb: int = 1024
    max_document_xml_mb: float = 512
    max_auxiliary_xml_mb: float = 64
    max_image_pixels: int = 200_000_000
    max_image_dimension_px: int = 30000
    max_svg_size_mb: float = 20
    # libxml2 "huge" mode lifts depth/text-node guards; keep off unless real documents need it.
    xml_huge_tree: bool = False
    sanitize_svg: bool = True
    reject_unsafe_xml: bool = True
    reject_macro_documents: bool = True
    reject_encrypted_entries: bool = True
    auth_enabled: bool = True
    api_key_header: str = "X-API-Key"
    # SHA-256 hex digests of accepted API keys. Never store plain keys in config.
    api_key_sha256: list[str] = Field(default_factory=list)
    rate_limit_requests_per_minute: int = 30

    @property
    def max_uncompressed_bytes(self) -> int:
        return int(self.max_uncompressed_size_mb * MB)

    @property
    def max_document_xml_bytes(self) -> int:
        return int(self.max_document_xml_mb * MB)

    @property
    def max_auxiliary_xml_bytes(self) -> int:
        return int(self.max_auxiliary_xml_mb * MB)

    @field_validator("api_key_sha256")
    @classmethod
    def _validate_hashes(cls, value: list[str]) -> list[str]:
        for digest in value:
            if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest.lower()):
                raise ValueError("api_key_sha256 entries must be 64-char hex SHA-256 digests")
        return [d.lower() for d in value]


class AnalysisSettings(BaseModel):
    # Factors for the rough HTML size estimate (see docs/performance.md).
    markup_overhead_factor: float = 2.5
    equation_html_bytes_estimate: int = 3000
    converted_vector_bytes_per_pixel: float = 0.6
    vector_render_dpi: int = 150
    include_text_samples: bool = False
    text_sample_chars: int = 60


class LoggingSettings(BaseModel):
    level: str = "INFO"
    json_format: bool = True


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="PATENT__", env_nested_delimiter="__", extra="ignore")

    environment: str = "production"
    claim_patterns_file: Path = PROJECT_ROOT / "config" / "claim_patterns.yaml"
    default_config_profile: str = "example"
    processing: ProcessingSettings = Field(default_factory=ProcessingSettings)
    storage: StorageSettings = Field(default_factory=StorageSettings)
    output: OutputSettings = Field(default_factory=OutputSettings)
    security: SecuritySettings = Field(default_factory=SecuritySettings)
    analysis: AnalysisSettings = Field(default_factory=AnalysisSettings)
    logging: LoggingSettings = Field(default_factory=LoggingSettings)

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        # Priority: explicit init kwargs > environment > YAML file (passed as init kwargs by load_settings).
        return (env_settings, init_settings, file_secret_settings)

    @model_validator(mode="after")
    def _startup_validation(self) -> "Settings":
        p = self.processing
        if p.max_combined_upload_mb < max(p.max_english_file_size_mb, p.max_arabic_file_size_mb):
            raise ValueError("processing.max_combined_upload_mb is smaller than a single-file limit")
        if p.max_concurrent_jobs_per_worker < 1:
            raise ValueError("processing.max_concurrent_jobs_per_worker must be >= 1")
        if self.environment == "production" and self.security.auth_enabled and not self.security.api_key_sha256:
            raise ValueError("production with auth_enabled requires at least one security.api_key_sha256")
        return self


def _read_yaml(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    if not isinstance(data, dict):
        raise ValueError(f"Configuration file {path} must contain a mapping")
    return data


def load_settings(config_file: Path | str | None = None, **overrides: Any) -> Settings:
    path = Path(config_file or os.environ.get("PATENT_CONFIG_FILE") or DEFAULT_CONFIG_FILE)
    data = _read_yaml(path) if path.exists() else {}
    if "claim_patterns_file" in data and not Path(data["claim_patterns_file"]).is_absolute():
        data["claim_patterns_file"] = str((path.parent / data["claim_patterns_file"]).resolve())
    data.update(overrides)
    return Settings(**data)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return load_settings()
