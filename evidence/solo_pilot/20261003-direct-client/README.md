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

A second diagnostic used the already completed quality call
`fc-01M3YQZ7V6AQA075F29G3FQP5G` from the aborted October 3 run. It
verified all 64 old-format staged documents, copied their exact bytes to a
separate root, `preflight-direct/f1d9f01975fb46999ea13008025d9681`, and
read back the copy. Both source and destination remote-receipt digests were
`sha256:0a8b551f1dd64302e998a4722c8ce90b5b6ec854646322d079465f802e9de172`.
No hidden content or score was printed, and the aborted run's files and
original evidence root were not modified. This qualifies old-format recovery
and direct copying, not the new single-bundle staging format or full scoring.
