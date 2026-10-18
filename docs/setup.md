# Environment setup

DocPilot reads its configuration from the environment. Create the resources below under your own names, store the two secrets in Key Vault, then copy those values and your resource names into an environment file.

Placeholder names in this guide (`keyVaultName`, `foundryAccountName`, and the rest) are labels. Substitute the names you chose. Subscription ids, resource names, and secret values stay in your own files.

One resource group. Put every resource in **East US 2**, except Azure AI Search, which goes in **East US**. East US 2 had no Free-tier Search capacity.

The app authenticates with `DefaultAzureCredential`. It does not take an API key. Locally that is `az login`. In Azure it is the container app's system-assigned managed identity.

## Resources

### Log Analytics — `logAnalyticsName`

| Setting | Value |
| --- | --- |
| Region | East US 2 |
| SKU | PerGB2018 |
| Retention | 30 days |
| Daily ingestion cap | 0.1 GB |

### Application Insights — `appInsightsName`

| Setting | Value |
| --- | --- |
| Region | East US 2 |
| Kind | web |
| Ingestion | Workspace-based, linked to `logAnalyticsName` |

Copy this resource's connection string into `APPLICATIONINSIGHTS_CONNECTION_STRING`. Leave that variable empty on a local run; Compose sends traces to Jaeger instead.

### Azure AI Search — `searchServiceName`

| Setting | Value |
| --- | --- |
| Region | East US |
| SKU | Free (1 replica, 1 partition) |
| Hosting mode | default |
| Public network access | Enabled |
| Semantic search | Free |
| API access control | Both (Azure AD and API keys) |

A subscription can have only one Free Search service. The app creates the index on the first ingest. The default index name is `docpilot` (`AZURE_SEARCH_INDEX`). The vector field is 512 dimensions. The semantic configuration name is `docpilot-semantic`.

Endpoint: `https://searchServiceName.search.windows.net`

### Microsoft Foundry — `foundryAccountName`

| Setting | Value |
| --- | --- |
| Region | East US 2 |
| Kind | AIServices |
| SKU | S0 |
| Custom subdomain | `foundryAccountName` |
| Public network access | Enabled |
| Project management | Enabled |

Project `foundryProjectName` on that account. Display name and description are yours.

Two deployments on the account:

| Deployment name | Model | Version | SKU | Capacity |
| --- | --- | --- | --- | --- |
| `gpt-6.1-sol` | gpt-6.1-sol | 2026-09-29 | Data Zone Standard | 50 |
| `text-embedding-3-small` | text-embedding-3-small | 1 | Data Zone Standard | 500 |

Chat and the judge call `gpt-6.1-sol`. Embeddings call `text-embedding-3-small` and request 512 dimensions. If you name a deployment something else, put that name in the matching variable in `.env`.

Endpoint: `https://foundryAccountName.cognitiveservices.azure.com/`

### Storage — `storageAccountName`

| Setting | Value |
| --- | --- |
| Region | East US 2 |
| SKU | Standard_LRS |
| Kind | StorageV2 |
| Access tier | Hot |
| Public blob access | Disabled |
| Shared key access | Disabled |
| Minimum TLS | 1.2 |
| HTTPS only | Yes |
| Public network access | Enabled |

One private blob container, named `sources`.

Endpoint: `https://storageAccountName.blob.core.windows.net`

With `AZURE_STORAGE_ACCOUNT_URL` empty, the app stores files under `DOCPILOT_DATA_DIR` (default `.data`). Compose uses Azurite instead, and that connection string overrides an account URL.

### Key Vault — `keyVaultName`

| Setting | Value |
| --- | --- |
| Region | East US 2 |
| SKU | Standard |
| Authorization | Azure RBAC |
| Soft delete | Enabled, 7 days |
| Public network access | Enabled |

Your user needs **Key Vault Secrets Officer** on this vault to create the secrets. The container app's managed identity needs **Key Vault Secrets User**.

Create these secrets. The names below are labels in the vault. The app reads the environment variables, so copy each value into `.env` and into the container app's environment.

| Secret | Environment variable | What it is |
| --- | --- | --- |
| `admin-password-hash` | `ADMIN_PASSWORD_HASH` | An argon2id hash of the `/admin` password. The password itself is never stored. Generate the hash with `uv run python -m docpilot.admin.hash_password`. The setup form on `/admin` requires at least 8 characters. |
| `session-secret` | `SESSION_SECRET` | The secret that signs the admin session cookie. Use a long random string. It is required when `ADMIN_PASSWORD_HASH` is set. |

Leave both empty to let the first visitor of `/admin` choose the password. The app then stores the hash and a generated session secret in the `sources` container. A value set in the environment wins over that stored pair.

### Container Apps — `containerAppsEnvironmentName` and `containerAppName`

Environment `containerAppsEnvironmentName`, East US 2, Consumption workload profile, logs sent to `logAnalyticsName`.

Container app `containerAppName`:

| Setting | Value |
| --- | --- |
| Identity | System-assigned |
| CPU | 0.5 |
| Memory | 1 Gi |
| Scale | Minimum 0, maximum 2 |
| Ingress | External, target port 8000 |
| Liveness probe | `GET /health` |

Set `DOCPILOT_COOKIE_SECURE` to `true` on this app. Local HTTP leaves it empty or `false`.

Grant the container app's managed identity:

| Role | Scope |
| --- | --- |
| Cognitive Services User | `foundryAccountName` |
| Search Index Data Contributor | `searchServiceName` |
| Search Service Contributor | `searchServiceName` |
| Storage Blob Data Contributor | `storageAccountName` |
| Key Vault Secrets User | `keyVaultName` |

A local `az login` user who uploads documents or calls `/ask` needs the same Foundry, Search, and Storage roles. Search Service Contributor is what lets that identity create the index.

## Environment file

```bash
cp .env.example .env
```

`.env` is gitignored. Fill it with your names:

| Variable | Value |
| --- | --- |
| `AZURE_OPENAI_ENDPOINT` | `https://foundryAccountName.cognitiveservices.azure.com/` |
| `AZURE_OPENAI_CHAT_DEPLOYMENT` | `gpt-6.1-sol`, or the chat deployment name you chose |
| `AZURE_OPENAI_EMBEDDING_DEPLOYMENT` | `text-embedding-3-small`, or the embeddings deployment name you chose |
| `AZURE_SEARCH_ENDPOINT` | `https://searchServiceName.search.windows.net` |
| `AZURE_SEARCH_INDEX` | `docpilot` |
| `AZURE_STORAGE_ACCOUNT_URL` | `https://storageAccountName.blob.core.windows.net` |
| `AZURE_STORAGE_CONTAINER` | `sources` |
| `AZURE_STORAGE_CONNECTION_STRING` | Empty when using the Azure account. Compose sets Azurite's development connection string itself. |
| `ADMIN_PASSWORD_HASH` | The `admin-password-hash` secret. Empty uses the first-visitor password. |
| `SESSION_SECRET` | The `session-secret` secret. Empty until a password is saved, or required when the hash is set. |
| `APPLICATIONINSIGHTS_CONNECTION_STRING` | The connection string from `appInsightsName`. Empty skips Azure Monitor. |
| `DOCPILOT_GA_MEASUREMENT_ID` | Google Analytics 4 measurement ID, such as `G-XXXXXXXXXX`. Empty skips the tag. Copy it into the container app environment so the live site sends hits. |
| `DOCPILOT_COOKIE_SECURE` | Empty or `false` for local HTTP. `true` behind HTTPS. |

The other names in `.env.example` have defaults: question length, rate limit, daily token cap, retrieval sizes, eval thresholds, and the agent step limit.

Run the app from the repo root and it loads `.env`. Existing environment variables win over the file.

`docker compose up --build` starts the API, Azurite, and Jaeger without this file. That process can serve pages and accept an admin upload into Azurite. Ask, the agent, and ingest against your Azure resources need the file inside the API container. Add `env_file: .env` under the `api` service, as described in [docker.md](docker.md). The Compose `environment` block still wins for Jaeger, the cookie flag, and the Azurite connection string.

## Documents

Starting the app does not index a corpus. An admin uploads PDFs on `/admin`. Public `/ask` and `/agent` answer from whatever has been uploaded.
