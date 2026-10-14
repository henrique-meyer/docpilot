# docpilot
AI document assistant in **Python**: Retrieval-Augmented Generation (RAG) Q&A with citations and a tool-using agent built on **Microsoft Agent Framework** (the successor to Semantic Kernel), **Azure OpenAI**, and **Azure AI Search**. It ships with an evaluation harness, Docker, and CI/CD, and deploys to **Azure Container Apps**.

Live demo: *(coming soon)*

## What it does
- Upload any set of documents (PDF) through a password-protected admin page; DocPilot chunks, embeds, and indexes them.
- Ask questions in plain English and get grounded answers with page-level citations.
- Agent mode: an LLM agent plans multi-step lookups, calls tools (search, fetch section, compare documents), and shows every tool call for auditability.
- Ships with public-domain sample sets (IRS publications, National Park Service guides, Medicare, Ready.gov).

## Highlights
- **RAG pipeline:** hybrid (vector + keyword) retrieval on Azure AI Search, `text-embedding-3-small` embeddings, context engineering with token budgets, grounded prompting, and "not found" handling.
- **Agentic AI:** tool calling and orchestration with Microsoft Agent Framework, plus guardrails (step limits, timeouts, scope refusals).
- **Evals as a quality gate:** golden-set regression tests, LLM-as-judge scoring, red-team prompts (prompt injection, jailbreaks, made-up citations), and CI that fails the PR when quality drops. A LangChain baseline is included for comparison.
- **Observability:** OpenTelemetry tracing across retrieval, LLM, and tool spans, with token and latency metrics.
- **Production practices:** containerized FastAPI microservice, infrastructure as code, secrets in Key Vault, OIDC to Azure (no stored credentials), rate limiting and token caps, unit tests, typed Python, and a cost budget with teardown.

## How it was built: human-led, AI-accelerated
DocPilot was built with an **AI-native development workflow**: AI agents do much of the heavy lifting, while a senior engineer owns every decision.

1. **Requirements with Grok Bot.** A personal AI assistant helped turn the idea into detailed product and technical requirements, then iterated on them through conversation.
2. **Planning with Cursor.** An AI coding agent turned the requirements into an architecture doc, a technical project plan, and a Jira backlog (epics and stories with DOCPILOT-N keys, linked to GitHub through smart commits).
3. **Infrastructure with Cursor.** The agent wrote infrastructure as code and provisioned Azure resources through the Azure CLI, behind preview-and-approve gates for anything that costs money.
4. **Implementation with Cursor.** The agent writes features against Jira stories in small PRs; CI runs tests and evals on every change.

**The human in the loop.** I acted as architect, product owner, and reviewer. I set the scope and constraints (cost ceiling, security, public-only data, a document-agnostic design), made the key technical calls (Python, Agent Framework over Semantic Kernel, Container Apps, a simple admin instead of a fixed corpus), caught gaps (an overly technical demo corpus, conflicting docs), approved every cloud change, and reviewed and merged every PR. The AI moves fast; the engineer decides what is right, safe, and worth shipping.

## Tech stack
Python 3.12 · FastAPI · Microsoft Agent Framework · Azure OpenAI · Azure AI Search · LangChain (baseline) · OpenTelemetry · Docker · Azure Container Apps · Azure Key Vault · GitHub Actions · Jira · Cursor · Grok Bot

## Azure resources
One resource group, in East US 2. The group name, subscription, and any custom domain stay out of this repo.

- Azure OpenAI, with a chat deployment (`gpt-4.1-mini`) and an embeddings deployment (`text-embedding-3-small` at 512 dimensions)
- Azure AI Search on the Free tier, in East US, because East US 2 had no free-tier capacity
- A storage account with shared keys disabled, and one private blob container for source PDFs
- Key Vault, for the admin password hash and the session secret
- Log Analytics and Application Insights
- A Container Apps environment and one Container App on the Consumption plan, scaled to zero
- A system-assigned managed identity, with data roles on OpenAI, Search, Key Vault, and Blob Storage
- A $10 budget alert on the resource group

Container images are published to GitHub Container Registry. There is no Azure Container Registry.

## Status
In active development. Architecture, setup, eval results, and a walkthrough will be added as the build progresses.

## License
MIT
