# Demo and interview guide

## A five-minute demonstration

Use a fresh demo-mode database or the isolated browser regression. Do not demonstrate with personal conversation history or provider keys visible on screen.

1. **Meeting:** Ask for a weekend family trip with an 8,000 budget and school constraints. Show independent expert stages, attributed disagreements and missing information.
2. **Human control:** Confirm a discussion conclusion; show that no tasks are created automatically. Request task extraction and approve the separate proposal.
3. **Private planning:** Save a synthetic learning preference, enable memory in a personal plan, expand its actual input source, then delete the memory and show the unavailable state.
4. **Protocol:** Search the bundled MCP reference library and show stdio/protocol/source information. Clarify that the reference corpus is local, not web search.
5. **Recovery:** Show pause/resume and the test evidence for process death, lease expiry and late-response fencing. Do not deliberately kill the user's real service during the demo.

The reproducible browser flow covers desktop and mobile. Synthetic screenshots and a video are linked from README when generated; model outputs in a demo recording are deterministic examples.

## Resume wording supported by the implementation

**FamilyAgent — personal multi-agent collaboration platform**  
Python / FastAPI / React / TypeScript / SQLite / MCP / Docker / GitHub Actions

- Designed a persistent role-agent Harness with independent first-round analysis, attributed disagreement validation, intent routing and explicit human approval of decisions/tasks.
- Implemented durable checkpoints, atomic task approval writes, per-attempt fencing, bounded lease renewal and independent-process crash/takeover tests to prevent stale responses from overwriting accepted state.
- Built authorized lexical memory retrieval with source/deletion states, bounded context assembly, an access-controlled tool registry and an actual MCP stdio integration.
- Added versioned rollback-safe migrations, dedicated run/message tables, cursor pagination and offline quality gates; deployment/CI and real-model results must be described using their verified status in the release review.

Do not insert unmeasured accuracy, cost, latency, availability or throughput percentages. Test counts are evidence of executed checks, not product quality scores. Update them only after a full run.

## Questions worth preparing

| Question | Explain using this project |
| --- | --- |
| Why multiple agents? | Different roles can expose conflicting assumptions; actual benefit requires paired quality review against a single-agent baseline, with extra computation disclosed |
| What is agent-to-agent communication here? | Structured role results and attributed findings inside one Harness; not a claim of standard A2A |
| How do you prevent duplicated effects? | Atomic approval transaction and stable task IDs; read/model effects can physically repeat after a crash, while accepted results are fenced |
| Why no transaction around the tool? | Remote waiting would hold SQLite's writer lock; persist intent first, wait outside, then revalidate ownership and authorization |
| Why heartbeat plus attempt ID? | Heartbeat keeps healthy work owned, deadline bounds stuck attempts, and attempt ID rejects old results even for the same Worker |
| What does CI prove? | Regression behavior, build and synthetic engineering checks; not real-provider quality, production security or scale |
| What would you scale next? | Measure contention/queue latency, then choose queue/database changes based on a real bottleneck rather than adding infrastructure first |
