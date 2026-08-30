"""Deterministic served-response correctness evaluation."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from ..canonical import DuplicateKeyError, digest_bytes, digest_value, parse_json


_CHECK_KINDS = {"exact", "regex", "casefold_exact"}


class CorrectnessValidationError(ValueError):
    """A correctness workload or served response is invalid."""


@dataclass(frozen=True, slots=True)
class CorrectnessCase:
    case_id: str
    messages: tuple[Mapping[str, str], ...]
    max_tokens: int
    check_kind: str
    expected: str

    def request(self, served_model_name: str) -> dict[str, object]:
        if not served_model_name:
            raise CorrectnessValidationError("served model name is required")
        return {
            "model": served_model_name,
            "messages": [dict(message) for message in self.messages],
            "max_tokens": self.max_tokens,
            "stream": False,
            "temperature": 0,
            "top_p": 1,
            "seed": 1729,
            "chat_template_kwargs": {"enable_thinking": False},
        }


@dataclass(frozen=True, slots=True)
class CorrectnessWorkload:
    digest: str
    cases: tuple[CorrectnessCase, ...]

    def requests(self, served_model_name: str) -> tuple[dict[str, object], ...]:
        return tuple(case.request(served_model_name) for case in self.cases)


@dataclass(frozen=True, slots=True)
class CorrectnessResult:
    workload_digest: str
    eligible: bool
    passed_cases: int
    total_cases: int
    failures: tuple[str, ...]
    response_digests: Mapping[str, str]
    evidence_digest: str

    def to_document(self) -> dict[str, object]:
        return {
            "workload_digest": self.workload_digest,
            "eligible": self.eligible,
            "passed_cases": self.passed_cases,
            "total_cases": self.total_cases,
            "failures": list(self.failures),
            "response_digests": dict(self.response_digests),
            "evidence_digest": self.evidence_digest,
        }


def load_correctness_workload(path: Path) -> CorrectnessWorkload:
    """Load a canonical JSON-lines correctness workload."""

    content = path.read_bytes()
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError as error:
        raise CorrectnessValidationError(
            "correctness workload must be UTF-8"
        ) from error
    lines = text.splitlines()
    if not lines or any(not line for line in lines):
        raise CorrectnessValidationError("correctness workload is empty or sparse")
    cases: list[CorrectnessCase] = []
    identifiers: set[str] = set()
    for line in lines:
        try:
            value = parse_json(line)
        except (json.JSONDecodeError, DuplicateKeyError) as error:
            raise CorrectnessValidationError(
                "correctness request is ambiguous"
            ) from error
        case = _case(value)
        if case.case_id in identifiers:
            raise CorrectnessValidationError("correctness case IDs repeat")
        identifiers.add(case.case_id)
        cases.append(case)
    return CorrectnessWorkload(digest_bytes(content), tuple(cases))


def score_correctness_responses(
    workload: CorrectnessWorkload,
    responses: Mapping[str, bytes],
    *,
    served_model_name: str,
) -> CorrectnessResult:
    """Validate raw OpenAI-compatible responses and apply registered checks."""

    if not served_model_name:
        raise CorrectnessValidationError("served model name is required")
    expected_ids = {case.case_id for case in workload.cases}
    if set(responses) != expected_ids:
        raise CorrectnessValidationError("correctness response case set differs")
    failures: list[str] = []
    passed = 0
    response_digests: dict[str, str] = {}
    for case in workload.cases:
        raw = responses[case.case_id]
        if not isinstance(raw, bytes):
            raise CorrectnessValidationError("correctness responses must be bytes")
        response_digests[case.case_id] = digest_bytes(raw)
        try:
            content = _response_content(raw, served_model_name)
        except CorrectnessValidationError:
            failures.append(f"{case.case_id}:api_schema")
            continue
        if _matches(case, content):
            passed += 1
        else:
            failures.append(f"{case.case_id}:check_failed")
    result_authority = {
        "workload_digest": workload.digest,
        "served_model_name": served_model_name,
        "eligible": not failures,
        "passed_cases": passed,
        "total_cases": len(workload.cases),
        "failures": failures,
        "response_digests": response_digests,
    }
    return CorrectnessResult(
        workload_digest=workload.digest,
        eligible=not failures,
        passed_cases=passed,
        total_cases=len(workload.cases),
        failures=tuple(failures),
        response_digests=response_digests,
        evidence_digest=digest_value(result_authority),
    )


def _case(value: object) -> CorrectnessCase:
    if not isinstance(value, dict) or set(value) != {
        "id",
        "messages",
        "max_tokens",
        "check",
    }:
        raise CorrectnessValidationError("correctness request fields differ")
    case_id = value["id"]
    max_tokens = value["max_tokens"]
    messages = value["messages"]
    check = value["check"]
    if not isinstance(case_id, str) or not case_id:
        raise CorrectnessValidationError("correctness case ID is invalid")
    if type(max_tokens) is not int or not 1 <= max_tokens <= 4096:
        raise CorrectnessValidationError("correctness token limit is invalid")
    if not isinstance(messages, list) or len(messages) != 1:
        raise CorrectnessValidationError("correctness messages are invalid")
    normalized_messages: list[Mapping[str, str]] = []
    for message in messages:
        if (
            not isinstance(message, dict)
            or set(message) != {"role", "content"}
            or message.get("role") != "user"
            or not isinstance(message.get("content"), str)
            or not message["content"]
        ):
            raise CorrectnessValidationError("correctness message is invalid")
        normalized_messages.append(
            {"role": str(message["role"]), "content": str(message["content"])}
        )
    if (
        not isinstance(check, dict)
        or set(check) != {"kind", "value"}
        or check.get("kind") not in _CHECK_KINDS
        or not isinstance(check.get("value"), str)
        or not check["value"]
    ):
        raise CorrectnessValidationError("correctness check is invalid")
    if check["kind"] == "regex":
        try:
            re.compile(str(check["value"]))
        except re.error as error:
            raise CorrectnessValidationError(
                "correctness regex is invalid"
            ) from error
    return CorrectnessCase(
        case_id=case_id,
        messages=tuple(normalized_messages),
        max_tokens=max_tokens,
        check_kind=str(check["kind"]),
        expected=str(check["value"]),
    )


def _response_content(raw: bytes, served_model_name: str) -> str:
    try:
        value = parse_json(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError, DuplicateKeyError) as error:
        raise CorrectnessValidationError(
            "correctness response is ambiguous"
        ) from error
    if not isinstance(value, dict) or value.get("model") != served_model_name:
        raise CorrectnessValidationError("correctness response model differs")
    choices = value.get("choices")
    if not isinstance(choices, list) or len(choices) != 1:
        raise CorrectnessValidationError("correctness response choices differ")
    choice = choices[0]
    if not isinstance(choice, dict) or choice.get("finish_reason") != "stop":
        raise CorrectnessValidationError("correctness response did not stop")
    message = choice.get("message")
    if (
        not isinstance(message, dict)
        or message.get("role") != "assistant"
        or not isinstance(message.get("content"), str)
    ):
        raise CorrectnessValidationError("correctness response message differs")
    return str(message["content"])


def _matches(case: CorrectnessCase, content: str) -> bool:
    if case.check_kind == "exact":
        return content == case.expected
    if case.check_kind == "casefold_exact":
        return content.casefold() == case.expected.casefold()
    return re.fullmatch(case.expected, content) is not None
