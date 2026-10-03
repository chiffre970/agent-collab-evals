# Direct-client Volume conformance, October 3

This is a diagnostic check, not a scored experiment. A local client in Modal's
`dev` environment uploaded 64 synthetic JSON documents to the private
`agent-collab-evals-evaluator-evidence-v2` Volume at
`preflight-direct/4b8ab458b7b74d408f7a4e92d0b01d29`. It read back the
manifest, receipt, and all 64 documents and checked exact byte equality.
The command started no Modal App, GPU function, or model request. The first
attempt could not contact Modal because the local network sandbox blocked
DNS; the approved network-enabled retry completed successfully. It did not
read or alter the aborted `solo-statusprobe-1003` audit or its staged result.

This proves the direct Volume API copy/read path. It does not prove the new
single-bundle staging path on Modal, the complete collector against a real
scored call, or a scoreable solo run. The root is retained for independent
read-only verification; do not treat its storage use as a settled bill.
