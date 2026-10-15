# Plain-code retrieval

`POST /ask` retrieves and prompts in ordinary Python. Hybrid search, top-k, the token budget, deduplication, and ordering are visible in `src/docpilot/retrieve`. The grounded prompt is the versioned file `answer_v1.txt`.

A framework chain would hide those steps. The agent path is the place for tool orchestration. Retrieval stays readable so a reviewer can see why a citation was chosen, and so a unit test can mock Search and the embedding call.
