import hashlib
import json
import logging
import os
import subprocess
import time
from collections.abc import Sequence
from typing import Any, Protocol

import httpx
from pydantic import BaseModel, Field

from glintory.config import settings
from glintory.domain.validation_models import (
    BilingualOpportunityBrief,
    EnglishBrief,
    JapaneseBrief,
)

logger = logging.getLogger(__name__)


from dataclasses import dataclass


@dataclass(frozen=True)
class LocalLlmRuntimeDescriptor:
    version: str
    commit: str | None
    binary_sha256: str


class OpportunityEnrichmentRequest(BaseModel):
    opportunity_id: str
    title: str
    summary: str
    evidence_count: int
    confidence: str
    evidence: list[dict[str, Any]]


class OpportunityEnrichmentResponse(BaseModel):
    status: str  # succeeded, failed, invalid_output, skipped
    error_code: str | None = None
    english: EnglishBrief | None = None
    japanese: JapaneseBrief | None = None
    evidence_refs: list[str] = Field(default_factory=list)
    llm_confidence: str | None = None
    duration_ms: int = 0


class OpportunityEnrichmentProvider(Protocol):
    def enrich_many(
        self,
        requests: Sequence[OpportunityEnrichmentRequest],
    ) -> Sequence[OpportunityEnrichmentResponse]: ...


def verify_sha256(filepath: str, expected_sha256: str) -> bool:
    if not expected_sha256:
        return False
    sha256_hash = hashlib.sha256()
    try:
        with open(filepath, "rb") as f:
            for byte_block in iter(lambda: f.read(4096), b""):
                sha256_hash.update(byte_block)
        return sha256_hash.hexdigest().lower() == expected_sha256.lower()
    except FileNotFoundError:
        return False


class LlamaServerContext:
    def __init__(
        self,
        binary_path: str,
        model_path: str,
        host: str = "127.0.0.1",
        port: int = 8088,
        timeout_seconds: int = 30,
    ) -> None:
        self.binary_path = binary_path
        self.model_path = model_path
        self.host = host
        self.port = port
        self.timeout_seconds = timeout_seconds
        self.process: subprocess.Popen | None = None

    def __enter__(self) -> "LlamaServerContext":
        if not os.path.exists(self.binary_path):
            raise FileNotFoundError("LLM_RUNTIME_START_FAILED")
        if not os.path.exists(self.model_path):
            raise FileNotFoundError("LLM_RUNTIME_START_FAILED")

        cmd = [
            self.binary_path,
            "--model",
            self.model_path,
            "--host",
            self.host,
            "--port",
            str(self.port),
            "--ctx-size",
            "4096",
            "--threads",
            "4",
            "--n-gpu-layers",
            "0",
            "--jinja",
            "--no-warmup",
        ]
        
        # Write server output to a log file for debugging on failure
        binary_dir = os.path.dirname(os.path.abspath(self.binary_path))
        build_dir = os.path.abspath("build")
        os.makedirs(build_dir, exist_ok=True)
        log_path = os.path.join(build_dir, "llama_server.log")
        log_file = open(log_path, "w", encoding="utf-8")
        
        env = os.environ.copy()
        ld_path = env.get("LD_LIBRARY_PATH", "")
        if ld_path:
            env["LD_LIBRARY_PATH"] = f"{binary_dir}:{ld_path}"
        else:
            env["LD_LIBRARY_PATH"] = binary_dir

        self.process = subprocess.Popen(
            cmd,
            stdout=log_file,
            stderr=log_file,
            text=True,
            env=env,
        )

        start_time = time.perf_counter()
        url = f"http://{self.host}:{self.port}/health"
        try:
            while time.perf_counter() - start_time < self.timeout_seconds:
                if self.process.poll() is not None:
                    raise RuntimeError("LLM_RUNTIME_START_FAILED")
                try:
                    response = httpx.get(url, timeout=1.0)
                    if response.status_code == 200:
                        log_file.close()
                        return self
                except httpx.HTTPError:
                    pass
                time.sleep(0.5)

            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()
            raise TimeoutError("LLM_RUNTIME_START_FAILED")
        finally:
            if not log_file.closed:
                log_file.close()

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        if self.process:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()


# JSON Schema for bilingual structure
BILINGUAL_ENRICHMENT_JSON_SCHEMA = {
    "type": "object",
    "properties": {
        "english": {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "summary": {"type": "string"},
                "problem_statement": {"type": "string"},
                "target_users": {"type": "array", "items": {"type": "string"}},
                "why_now": {"type": "string"},
                "evidence_synthesis": {"type": "string"},
                "build_direction": {"type": "string"},
                "risks": {"type": "array", "items": {"type": "string"}},
                "tags": {"type": "array", "items": {"type": "string"}},
            },
            "required": [
                "title",
                "summary",
                "problem_statement",
                "target_users",
                "why_now",
                "evidence_synthesis",
                "build_direction",
                "risks",
                "tags",
            ],
            "additionalProperties": False,
        },
        "japanese": {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "summary": {"type": "string"},
                "problem_statement": {"type": "string"},
                "target_users": {"type": "array", "items": {"type": "string"}},
                "why_now": {"type": "string"},
                "evidence_synthesis": {"type": "string"},
                "build_direction": {"type": "string"},
                "risks": {"type": "array", "items": {"type": "string"}},
                "tags": {"type": "array", "items": {"type": "string"}},
            },
            "required": [
                "title",
                "summary",
                "problem_statement",
                "target_users",
                "why_now",
                "evidence_synthesis",
                "build_direction",
                "risks",
                "tags",
            ],
            "additionalProperties": False,
        },
        "evidence_refs": {"type": "array", "items": {"type": "string"}},
        "confidence": {"type": "string", "enum": ["low", "medium", "high"]},
    },
    "required": ["english", "japanese", "evidence_refs", "confidence"],
    "additionalProperties": False,
}


class LocalLlmProvider:
    def __init__(
        self,
        binary_path: str | None = None,
        binary_sha256: str | None = None,
        model_path: str | None = None,
        model_sha256: str | None = None,
        host: str | None = None,
        port: int | None = None,
    ) -> None:
        self.binary_path = binary_path or settings.local_llm_binary_path
        self.binary_sha256 = binary_sha256 or settings.local_llm_binary_sha256
        self.model_path = model_path or settings.local_llm_model_path
        self.model_sha256 = model_sha256 or settings.local_llm_model_sha256
        self.host = host or settings.local_llm_bind_address
        self.port = port or settings.local_llm_port
        self.runtime_descriptor: LocalLlmRuntimeDescriptor | None = None

    def verify_infrastructure(self) -> None:
        if not os.path.exists(self.binary_path):
            logger.error("LLM_RUNTIME_START_FAILED")
            raise FileNotFoundError("LLM_RUNTIME_START_FAILED")
        if self.binary_sha256 and not verify_sha256(
            self.binary_path, self.binary_sha256
        ):
            logger.error("LLM_RUNTIME_START_FAILED")
            raise ValueError("LLM_RUNTIME_START_FAILED")

        try:
            binary_dir = os.path.dirname(os.path.abspath(self.binary_path))
            env = os.environ.copy()
            ld_path = env.get("LD_LIBRARY_PATH", "")
            if ld_path:
                env["LD_LIBRARY_PATH"] = f"{binary_dir}:{ld_path}"
            else:
                env["LD_LIBRARY_PATH"] = binary_dir

            res = subprocess.run(
                [self.binary_path, "--version"],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                timeout=5,
                check=False,
                env=env,
            )
            version_str = "unknown"
            commit_str = None
            for line in res.stdout.splitlines():
                if "version:" in line:
                    parts = line.split("version:")
                    if len(parts) > 1:
                        val = parts[1].strip()
                        version_str = val
                        if "(" in val and ")" in val:
                            inner = val.split("(")[1].split(")")[0].strip()
                            commit_str = inner
                            version_str = val.split("(")[0].strip()

            if version_str == "unknown":
                version_str = res.stdout.strip() or "unknown"

            expected_version = settings.local_llm_runtime_version
            expected_ver_num = expected_version.lstrip("b")
            if (
                expected_ver_num not in version_str
                and expected_version not in version_str
            ):
                logger.error("LLM_RUNTIME_START_FAILED")
                raise ValueError("LLM_RUNTIME_START_FAILED")

            commit_to_save = commit_str
            if settings.local_llm_runtime_commit:
                expected_commit = settings.local_llm_runtime_commit
                if not commit_str or not (
                    expected_commit.startswith(commit_str)
                    or commit_str.startswith(expected_commit)
                ):
                    logger.error("LLM_RUNTIME_START_FAILED")
                    raise ValueError("LLM_RUNTIME_START_FAILED")
                commit_to_save = expected_commit

            self.runtime_descriptor = LocalLlmRuntimeDescriptor(
                version=version_str, commit=commit_to_save, binary_sha256=self.binary_sha256
            )
        except Exception as e:
            if isinstance(e, ValueError) and str(e) == "LLM_RUNTIME_START_FAILED":
                raise
            logger.error("LLM_RUNTIME_START_FAILED")
            raise RuntimeError("LLM_RUNTIME_START_FAILED")

        if not os.path.exists(self.model_path):
            logger.error("LLM_RUNTIME_START_FAILED")
            raise FileNotFoundError("LLM_RUNTIME_START_FAILED")
        if self.model_sha256 and not verify_sha256(self.model_path, self.model_sha256):
            logger.error("LLM_RUNTIME_START_FAILED")
            raise ValueError("LLM_RUNTIME_START_FAILED")

    def enrich_many(
        self,
        requests: Sequence[OpportunityEnrichmentRequest],
    ) -> Sequence[OpportunityEnrichmentResponse]:
        if not requests:
            return []

        self.verify_infrastructure()

        responses = []
        try:
            with LlamaServerContext(
                binary_path=self.binary_path,
                model_path=self.model_path,
                host=self.host,
                port=self.port,
                timeout_seconds=30,
            ):
                for req in requests:
                    res = self._enrich_single(req)
                    responses.append(res)
        except Exception:
            logger.error("LLM_RUNTIME_START_FAILED")
            while len(responses) < len(requests):
                responses.append(
                    OpportunityEnrichmentResponse(
                        status="failed",
                        error_code="LLM_RUNTIME_START_FAILED",
                    )
                )
        return responses

    def _enrich_single(
        self,
        request: OpportunityEnrichmentRequest,
    ) -> OpportunityEnrichmentResponse:
        start_time = time.perf_counter()

        system_prompt = (
            "First create the canonical English brief from the supplied evidence.\n\n"
            "Then create a faithful and natural Japanese translation of the English brief.\n\n"
            "The Japanese version must not add, remove, strengthen, or weaken any factual claim.\n\n"
            "Both language versions must use the same evidence_refs and confidence.\n\n"
            "Use professional but readable Japanese.\n"
            "Avoid literal word-for-word translation when it sounds unnatural.\n"
            "Do not add market data, companies, numbers, or recommendations not present in the evidence.\n\n"
            "/no_think"
        )

        user_content = {
            "opportunity_id": request.opportunity_id,
            "title": request.title,
            "summary": request.summary,
            "confidence": request.confidence,
            "evidence_count": request.evidence_count,
            "evidence": request.evidence,
        }

        user_json_str = json.dumps(user_content, ensure_ascii=False)

        url = f"http://{self.host}:{self.port}/v1/chat/completions"
        payload = {
            "model": "local-model",
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_json_str},
            ],
            "response_format": {
                "type": "json_object",
                "schema": BILINGUAL_ENRICHMENT_JSON_SCHEMA,
            },
            "chat_template_kwargs": {
                "enable_thinking": False,
            },
            "max_tokens": settings.local_llm_max_output_tokens,
            "temperature": 0.0,
        }

        try:
            with httpx.Client(
                timeout=float(settings.local_llm_timeout_seconds)
            ) as client:
                response = client.post(url, json=payload)
                if response.status_code != 200:
                    logger.error("LLM_INFERENCE_FAILED")
                    return OpportunityEnrichmentResponse(
                        status="failed",
                        error_code="LLM_INFERENCE_FAILED",
                        duration_ms=int((time.perf_counter() - start_time) * 1000),
                    )

                try:
                    result = response.json()
                except Exception:
                    logger.error("LLM_INVALID_JSON")
                    return OpportunityEnrichmentResponse(
                        status="invalid_output",
                        error_code="LLM_INVALID_JSON",
                        duration_ms=int((time.perf_counter() - start_time) * 1000),
                    )

                if (
                    not isinstance(result, dict)
                    or "choices" not in result
                    or not result["choices"]
                ):
                    logger.error("LLM_SCHEMA_VALIDATION_FAILED")
                    return OpportunityEnrichmentResponse(
                        status="invalid_output",
                        error_code="LLM_SCHEMA_VALIDATION_FAILED",
                        duration_ms=int((time.perf_counter() - start_time) * 1000),
                    )

                content = result["choices"][0].get("message", {}).get("content")
                if not content:
                    logger.error("LLM_SCHEMA_VALIDATION_FAILED")
                    return OpportunityEnrichmentResponse(
                        status="invalid_output",
                        error_code="LLM_SCHEMA_VALIDATION_FAILED",
                        duration_ms=int((time.perf_counter() - start_time) * 1000),
                    )

                duration_ms = int((time.perf_counter() - start_time) * 1000)

                try:
                    data = json.loads(content)
                except json.JSONDecodeError:
                    logger.error("LLM_INVALID_JSON")
                    return OpportunityEnrichmentResponse(
                        status="invalid_output",
                        error_code="LLM_INVALID_JSON",
                        duration_ms=duration_ms,
                    )

                try:
                    brief = BilingualOpportunityBrief.model_validate(data)
                except Exception:
                    logger.error("LLM_SCHEMA_VALIDATION_FAILED")
                    return OpportunityEnrichmentResponse(
                        status="invalid_output",
                        error_code="LLM_SCHEMA_VALIDATION_FAILED",
                        duration_ms=duration_ms,
                    )

                try:
                    input_evidence_ids = {e["id"] for e in request.evidence}
                    for ref in brief.evidence_refs:
                        if ref not in input_evidence_ids:
                            logger.error("LLM_SCHEMA_VALIDATION_FAILED")
                            return OpportunityEnrichmentResponse(
                                status="invalid_output",
                                error_code="LLM_SCHEMA_VALIDATION_FAILED",
                                duration_ms=duration_ms,
                            )
                except Exception:
                    logger.error("LLM_SCHEMA_VALIDATION_FAILED")
                    return OpportunityEnrichmentResponse(
                        status="invalid_output",
                        error_code="LLM_SCHEMA_VALIDATION_FAILED",
                        duration_ms=duration_ms,
                    )

                return OpportunityEnrichmentResponse(
                    status="succeeded",
                    english=brief.english,
                    japanese=brief.japanese,
                    evidence_refs=brief.evidence_refs,
                    llm_confidence=brief.confidence,
                    duration_ms=duration_ms,
                )

        except httpx.TimeoutException:
            logger.error("LLM_TIMEOUT")
            return OpportunityEnrichmentResponse(
                status="failed",
                error_code="LLM_TIMEOUT",
                duration_ms=int((time.perf_counter() - start_time) * 1000),
            )
        except Exception:
            logger.error("LLM_INFERENCE_FAILED")
            return OpportunityEnrichmentResponse(
                status="failed",
                error_code="LLM_INFERENCE_FAILED",
                duration_ms=int((time.perf_counter() - start_time) * 1000),
            )
