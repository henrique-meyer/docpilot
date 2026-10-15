# Agent Framework, with a LangChain baseline

The production agent imports `agent_framework` from `agent-framework-core`, and the Foundry chat client comes from `agent-framework-openai`. That is the current Microsoft agent stack. Semantic Kernel is not a dependency, and the `agent-framework` meta-package is not installed.

The agent has three tools: `search_documents`, `get_section`, and `compare_documents`. A request is capped at six tool calls and thirty seconds. `eval/langchain_baseline.py` answers the same golden set with a LangChain RAG chain so the two can be compared. LangChain is a dev dependency. It is not in the production image.
