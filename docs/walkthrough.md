# Walkthrough

About six minutes, spoken.

## Problem

DocPilot answers questions from documents an admin uploaded. It does not ship with one fixed corpus. The live set is whatever is in the catalog. An admin upload is the way documents get in.

## Architecture

A browser talks to one FastAPI app. That app parses a PDF, chunks it, embeds the chunks, and stores the file. Locally the file goes to Azurite. In Azure it goes to Blob Storage, and the app uses its managed identity. Search is hybrid, on the Free tier. The semantic ranker stays on the free allowance, and search falls back to keyword plus vector ranking when that allowance is used up. Chat and the judge use `gpt-6.1-sol`. Embeddings are `text-embedding-3-small` at 512 dimensions.

There are two ways to ask. `/ask` retrieves passages and fills one versioned prompt. `/agent` lets Microsoft Agent Framework call three tools: search, fetch a section, and compare documents. Both paths cite the page. If the documents do not support an answer, the reply is "not found in the documents."

## RAG

Retrieval is plain code, not a framework chain. The retriever asks for the top passages, drops duplicates, and stops at a limit on how much context is sent. The prompt file tells the model to cite every claim and to ignore instructions that show up inside a document. That file is `answer_v1.txt`. Changing it is a visible diff.

A citation names the document, the section, and the page. The page shows those next to the answer. The disclaimer is that the answer comes from the uploaded documents and is informational only. If retrieval finds nothing, the model is not called, and the answer is the not-found sentence. That keeps an empty catalog from inventing a source.

## Agent

The agent is for a question that needs more than one lookup. It can search, open a section, and compare two publications on one topic. The response lists each tool call. A request stops after six calls or thirty seconds. Public `/ask` and `/agent` stay anonymous. They are rate limited, the question has a maximum length, and a daily token cap returns "demo limit reached."

## Admin

`/admin` is one password. The first visitor sets it. The hash is stored, never the password. After that, the page lists documents. An upload that would pass the index limit for that deployment is blocked. The person who sets the deployment up chooses that limit. Replace indexes the new file before the old chunks are removed. Delete removes the file and the chunks. Each action is an audit row: who, what, and when.

## Evals and CI

The golden set has 30 questions, including some that need more than one document, and 10 red-team prompts. The thresholds are correctness 0.80, citation accuracy 0.90, groundedness 0.80, and every red-team prompt passing. Those questions apply only when those documents are what an environment has indexed. `make eval` is the live run against that environment's index, and the report belongs to that environment. A LangChain script answers the same set for a side-by-side report. Pull-request CI lints, typechecks, runs the unit tests, and builds the image. It does not score the question file.

## Observability

OpenTelemetry spans cover the API, retrieval, the model, and each tool call. They carry token counts and latency, not the question text or the document body. Logs are JSON, with a trace id, and secrets are redacted. Compose sends spans to Jaeger. Azure sends them to the Application Insights resource already in the template. The workspace cap is 0.1 GB a day.

## Deployment

Merging to main builds the image, pushes it to GitHub Container Registry, and updates the container app. The app scales to zero. Teardown is deleting the resource group named in the local parameter file. The hostname and the certificate stay out of git.

## What to click

Start Compose, open the site, and set the admin password. Upload a PDF. Ask a question that the document answers, and check that the citation matches the page. Then ask something the document does not contain, and expect the not-found sentence. Open Jaeger and find the retrieval span for that request. The span has a hit count and a latency, not the question text.

## Lessons

The retrieval path stays in ordinary Python so a reviewer can read it. The agent framework is only the tool loop. The assistant answers only from documents indexed in a cloud environment, so a pull-request check is not an evaluation of a deployment. The product never hard-codes a corpus.
