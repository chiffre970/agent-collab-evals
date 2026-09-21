"""Separate repeated measurements across runs without remote compute."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

from agent_collab_evals.adapters.local_measurements import LocalMeasurementBundleStore, MeasurementBundleError
from agent_collab_evals.pilot_evidence import retain_document


class MeasurementNamespaceTests(unittest.TestCase):
    def test_identical_reference_in_new_run_has_distinct_remote_destination(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = LocalMeasurementBundleStore(root / "first")
            second = LocalMeasurementBundleStore(root / "second")
            first_id = first.remote_namespace("exec-same-reference")
            second_id = second.remote_namespace("exec-same-reference")
            self.assertNotEqual(first_id, second_id)
            # Each run can retain a distinct timing outcome without replacing
            # the previous run's evidence, for all three remote phase prefixes.
            for prefix in ("model-serving", "model-serving-quality", "model-serving-correctness"):
                retain_document(root / "volume" / prefix / first_id / "receipt.json", {"duration": 100})
                retain_document(root / "volume" / prefix / second_id / "receipt.json", {"duration": 101})
            self.assertEqual(LocalMeasurementBundleStore(root / "first").remote_namespace("exec-same-reference"), first_id)
            shutil.copytree(root / "first", root / "restored")
            self.assertEqual(LocalMeasurementBundleStore(root / "restored").remote_namespace("exec-same-reference"), first_id)

    def test_real_remote_writer_keeps_two_reference_runs_separate(self):
        from tests.test_modal_vllm_contracts import MODAL_VLLM

        with tempfile.TemporaryDirectory() as directory, patch.object(MODAL_VLLM.subprocess, "run"):
            root = Path(directory)
            paths = []
            for run, duration in (("first", 100), ("second", 101)):
                namespace = LocalMeasurementBundleStore(root / run).remote_namespace("exec-same-reference")
                evidence_root = f"model-serving/{namespace}/repetition-0001-attempt-01"
                destination = root / "volume" / evidence_root
                MODAL_VLLM._persist_evidence_at(destination=destination,
                    sync_root=root / "volume", volume_name="local-test", evidence_root=evidence_root,
                    remote_receipt={"duration": duration}, raw_results={"point.json": b'{}'})
                paths.append(destination / "remote-receipt.json")
            self.assertNotEqual(paths[0], paths[1])
            self.assertNotEqual(paths[0].read_bytes(), paths[1].read_bytes())

    def test_concurrent_collectors_share_one_durable_namespace(self):
        with tempfile.TemporaryDirectory() as directory:
            def read(_):
                return LocalMeasurementBundleStore(Path(directory)).remote_namespace("reference")
            with ThreadPoolExecutor(max_workers=4) as pool:
                identifiers = list(pool.map(read, range(12)))
            self.assertEqual(len(set(identifiers)), 1)
            other = LocalMeasurementBundleStore(Path(directory)).remote_namespace("candidate")
            self.assertNotEqual(other, identifiers[0])

    def test_legacy_dispatch_without_namespace_requires_original_collector(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            retain_document(root / ".dispatch/reference/dispatch.json", {"call_id": "old-call"})
            with self.assertRaisesRegex(MeasurementBundleError, "original collector"):
                LocalMeasurementBundleStore(root).remote_namespace("reference")
            self.assertFalse((root / ".remote-namespace.json").exists())
