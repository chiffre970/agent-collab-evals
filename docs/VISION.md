# Long-Term Product Vision

## A governed work network

The product thesis is a governed work network where humans and agents organise together.

Recent agent experiments have shown an intriguing possibility: when no intended coordination channel is supplied, agents may still discover ways to exchange information through shared metadata, comments or other incidental surfaces. It is not yet clear how common or capable this behavior will become, but larger fleets make inter-agent communication important enough to study deliberately.

If agents tend to coordinate through whatever useful shared systems are available, organisations need a sanctioned environment where that coordination can emerge while costs, permissions, resources, approvals, duplication and external actions remain controlled and auditable. The ambition is not merely safer agents. It is a more productive organisation: agents should be able to discover prior work, combine complementary efforts, reuse knowledge, route needs to specialists and reduce duplicated research or execution.

Unlike a workflow limited to human to agent to subagent delegation, work forms an evolving graph:

- Humans and agents delegate and collaborate bidirectionally.
- Missions fan out into parallel efforts.
- Independent efforts fan in around shared discoveries, resources or actions.
- Agents collaborate laterally, challenge results and reuse prior work.

A relevance engine could connect new work with the right agents, humans, artifacts and related efforts. It would index capabilities, missions, conversations, resources, decisions, actions and verified outcomes, ranking them by relevance, track record, availability, cost, authority and complementary expertise rather than engagement.

Agents, workspaces and organisational memory can persist across individual jobs. Over time, agents may develop useful specialisms, while verified artifacts and decisions become institutional knowledge that survives any one context window or agent. A sanctioned network should earn adoption by becoming the easiest place to find that knowledge, compute, tools and collaborators. This usefulness can create a discovery flywheel without relying on the unrealistic assumption that every alternative communication channel can be prohibited.

The system should provide a small set of safe primitives rather than prescribe one ideal organisation. New conventions for delegation, review, deconfliction and team formation should be observable and measurable; better agent-discovered conventions should be able to supersede weaker ones. Whether this learning actually occurs is a research question, not a product assumption.

Correlation is an important capability, not the whole pitch. Just as many alarms can indicate one underlying fault, many agents' local needs may converge on one organisational action. The system should preserve every originating need while preventing duplicated execution.

## Governance boundary

Open communication must not imply open authority. Delegating a request to a more capable or more privileged agent can amplify risk just as permission escalation can: the receiving agent can be persuaded, confused or supplied with malicious context.

Consequential services must therefore treat every agent request as untrusted. They need independent policy checks, scoped capabilities, explicit budgets, idempotency, approval thresholds and auditable receipts. A privileged procurement agent, for example, should not inherit the requester's judgment; it should apply procurement policy and escalate or refuse when required.

The core principle is:

> Let coordination emerge. Keep consequences controlled.

## Relationship to this repository

The full product could eventually expose an API, MCP server and agent skill, with a dashboard for humans. It could add organisational search, matching, deduplication, reputation, specialist capability routing and human approval workflows.

Those features are hypotheses, not prerequisites. This repository starts with the narrower question of whether a minimal peer collaboration surface improves verified work. One four-agent campaign cannot validate the whole product thesis: the research program must test different work structures, persistent organisations and increasing fleet sizes.

Benefits may become much larger—or coordination costs may become dominant—when an organisation has tens or roughly one hundred agents rather than four. V0 is an affordable causal starting point; scale-dependent productivity, specialization and institutional memory are later hypotheses to test rather than conclusions to assume.
