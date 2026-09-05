"""Provider-neutral delivery intents, runtime receipts, and reconciliation."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Mapping

from .canonical import digest_value
from .domain import Job, SessionHandle


_DIGEST = re.compile(r"sha256:[0-9a-f]{64}")
_RECEIPT_ID = re.compile(r"delivery-[0-9a-f]{32}")
_INTENT_ID = re.compile(r"intent-[0-9a-f]{32}")


@dataclass(frozen=True, slots=True)
class DeliveryIntent:
    intent_id: str
    campaign_run_id: str
    session: SessionHandle
    job: Job

    def __post_init__(self) -> None:
        if not _INTENT_ID.fullmatch(self.intent_id):
            raise ValueError("delivery intent ID is invalid")
        if not self.campaign_run_id or not self.session.value or not self.job.job_id:
            raise ValueError("delivery intent identity is incomplete")
        if self.intent_id != self.expected_intent_id:
            raise ValueError("delivery intent ID differs from its contents")

    @classmethod
    def create(
        cls, campaign_run_id: str, session: SessionHandle, job: Job
    ) -> "DeliveryIntent":
        stable_job = job_from_document(job_document(job))
        intent_id = "intent-" + digest_value(
            {
                "campaign_run_id": campaign_run_id,
                "session_id": session.value,
                "job": job_document(stable_job),
            }
        )[7:39]
        return cls(intent_id, campaign_run_id, session, stable_job)

    @property
    def expected_intent_id(self) -> str:
        return "intent-" + digest_value(
            {
                "campaign_run_id": self.campaign_run_id,
                "session_id": self.session.value,
                "job": job_document(self.job),
            }
        )[7:39]


@dataclass(frozen=True, slots=True)
class HarnessDeliveryReceipt:
    receipt_id: str
    session_id: str
    job_id: str
    materials_digest: str
    runtime_profile_digest: str
    acknowledgement: Mapping[str, Any]
    acknowledgement_digest: str

    def __post_init__(self) -> None:
        if not _RECEIPT_ID.fullmatch(self.receipt_id):
            raise ValueError("delivery receipt ID is invalid")
        if not self.session_id or not self.job_id or not self.materials_digest:
            raise ValueError("delivery receipt identity is incomplete")
        for value in (self.runtime_profile_digest, self.acknowledgement_digest):
            if not _DIGEST.fullmatch(value):
                raise ValueError("delivery receipt digest is invalid")
        if digest_value(self.acknowledgement) != self.acknowledgement_digest:
            raise ValueError("delivery acknowledgement digest differs")
        if self.receipt_id != self.expected_receipt_id:
            raise ValueError("delivery receipt ID differs from its contents")

    @classmethod
    def create(
        cls,
        session: SessionHandle,
        job: Job,
        *,
        runtime_profile_digest: str,
        acknowledgement: Mapping[str, Any],
    ) -> "HarnessDeliveryReceipt":
        acknowledgement_digest = digest_value(acknowledgement)
        receipt_id = _receipt_id(
            session.value,
            job.job_id,
            job.materials_digest,
            runtime_profile_digest,
            acknowledgement_digest,
        )
        return cls(
            receipt_id,
            session.value,
            job.job_id,
            job.materials_digest,
            runtime_profile_digest,
            dict(acknowledgement),
            acknowledgement_digest,
        )

    @property
    def expected_receipt_id(self) -> str:
        return _receipt_id(
            self.session_id,
            self.job_id,
            self.materials_digest,
            self.runtime_profile_digest,
            self.acknowledgement_digest,
        )

    @property
    def document(self) -> dict[str, Any]:
        return {
            "receipt_id": self.receipt_id,
            "session_id": self.session_id,
            "job_id": self.job_id,
            "materials_digest": self.materials_digest,
            "runtime_profile_digest": self.runtime_profile_digest,
            "acknowledgement": dict(self.acknowledgement),
            "acknowledgement_digest": self.acknowledgement_digest,
        }

    @classmethod
    def from_document(cls, value: Mapping[str, Any]) -> "HarnessDeliveryReceipt":
        expected = {
            "receipt_id",
            "session_id",
            "job_id",
            "materials_digest",
            "runtime_profile_digest",
            "acknowledgement",
            "acknowledgement_digest",
        }
        string_fields = expected - {"acknowledgement"}
        if (
            set(value) != expected
            or any(not isinstance(value[name], str) for name in string_fields)
            or not isinstance(value["acknowledgement"], dict)
        ):
            raise ValueError("delivery receipt fields differ")
        return cls(
            receipt_id=value["receipt_id"],
            session_id=value["session_id"],
            job_id=value["job_id"],
            materials_digest=value["materials_digest"],
            runtime_profile_digest=value["runtime_profile_digest"],
            acknowledgement=dict(value["acknowledgement"]),
            acknowledgement_digest=value["acknowledgement_digest"],
        )


@dataclass(frozen=True, slots=True)
class DeliveryReconciliation:
    campaign_run_id: str
    job_ids: tuple[str, ...]
    receipts: tuple[HarnessDeliveryReceipt, ...]
    evidence_digest: str

    def __post_init__(self) -> None:
        if not self.campaign_run_id:
            raise ValueError("delivery reconciliation campaign is required")
        if len(set(self.job_ids)) != len(self.job_ids):
            raise ValueError("delivery reconciliation jobs repeat")
        if not _DIGEST.fullmatch(self.evidence_digest):
            raise ValueError("delivery reconciliation digest is invalid")
        expected = digest_value(
            {
                "campaign_run_id": self.campaign_run_id,
                "job_ids": list(self.job_ids),
                "receipts": [receipt.document for receipt in self.receipts],
            }
        )
        if self.evidence_digest != expected:
            raise ValueError("delivery reconciliation digest differs")

    @classmethod
    def create(
        cls,
        campaign_run_id: str,
        job_ids: tuple[str, ...],
        receipts: tuple[HarnessDeliveryReceipt, ...],
    ) -> "DeliveryReconciliation":
        evidence_digest = digest_value(
            {
                "campaign_run_id": campaign_run_id,
                "job_ids": list(job_ids),
                "receipts": [receipt.document for receipt in receipts],
            }
        )
        return cls(campaign_run_id, job_ids, receipts, evidence_digest)


def job_document(job: Job) -> dict[str, Any]:
    return {
        "job_id": job.job_id,
        "mission": job.mission,
        "materials_digest": job.materials_digest,
        "public_materials": dict(job.public_materials),
    }


def job_from_document(value: Mapping[str, Any]) -> Job:
    expected = {"job_id", "mission", "materials_digest", "public_materials"}
    if set(value) != expected:
        raise ValueError("delivery job fields differ")
    public_materials = value["public_materials"]
    if not isinstance(public_materials, dict) or any(
        not isinstance(key, str) or not isinstance(item, str)
        for key, item in public_materials.items()
    ):
        raise ValueError("delivery public materials are invalid")
    for name in ("job_id", "mission", "materials_digest"):
        if not isinstance(value[name], str) or not value[name]:
            raise ValueError("delivery job identity is invalid")
    return Job(
        value["job_id"],
        value["mission"],
        value["materials_digest"],
        dict(public_materials),
    )


def _receipt_id(
    session_id: str,
    job_id: str,
    materials_digest: str,
    runtime_profile_digest: str,
    acknowledgement_digest: str,
) -> str:
    return "delivery-" + digest_value(
        {
            "session_id": session_id,
            "job_id": job_id,
            "materials_digest": materials_digest,
            "runtime_profile_digest": runtime_profile_digest,
            "acknowledgement_digest": acknowledgement_digest,
        }
    )[7:39]
