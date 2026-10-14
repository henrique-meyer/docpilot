targetScope = 'resourceGroup'

// Deploy into the resource group chosen on the command line.
// Subscription, alert email, and custom domain live in the gitignored parameter file.

@description('Region for every resource except Search.')
param location string = 'eastus2'

@description('Search region. East US 2 had no free-tier capacity on 14 October 2026, so Search is placed in East US inside the same resource group.')
param searchLocation string = 'eastus'

@description('Email that receives the $10 budget alerts.')
param budgetContactEmail string

@description('Image GitHub Actions publishes. A redeploy must keep this tag so the placeholder image is not restored.')
param containerImage string = 'ghcr.io/henrique-meyer/docpilot:prod'

@description('Port the container listens on.')
param targetPort int = 8000

@description('HTTP path the liveness probe requests.')
param healthPath string = '/health'

@description('Custom hostname already bound to the Container App.')
param customDomainName string = ''

@description('Managed certificate resource ID for the custom hostname. Empty leaves the binding out of this deploy.')
param customDomainCertificateId string = ''

var tags = {
  project: 'docpilot'
  environment: 'prod'
}

resource law 'Microsoft.OperationalInsights/workspaces@2023-09-01' = {
  name: 'log-docpilot-prod'
  location: location
  tags: tags
  properties: {
    sku: {
      name: 'PerGB2018'
    }
    retentionInDays: 30
    workspaceCapping: {
      dailyQuotaGb: json('0.1')
    }
  }
}

resource appInsights 'Microsoft.Insights/components@2020-02-02' = {
  name: 'appi-docpilot-prod'
  location: location
  tags: tags
  kind: 'web'
  properties: {
    Application_Type: 'web'
    WorkspaceResourceId: law.id
    IngestionMode: 'LogAnalytics'
  }
}

resource search 'Microsoft.Search/searchServices@2023-11-01' = {
  name: 'srch-docpilot-prod'
  location: searchLocation
  tags: tags
  sku: {
    name: 'free'
  }
  properties: {
    replicaCount: 1
    partitionCount: 1
    hostingMode: 'default'
    publicNetworkAccess: 'enabled'
    semanticSearch: 'free'
  }
}

resource openai 'Microsoft.CognitiveServices/accounts@2024-10-01' = {
  name: 'oai-docpilot-prod'
  location: location
  tags: tags
  kind: 'OpenAI'
  sku: {
    name: 'S0'
  }
  properties: {
    customSubDomainName: 'oai-docpilot-prod'
    publicNetworkAccess: 'Enabled'
  }
}

resource chatDeployment 'Microsoft.CognitiveServices/accounts/deployments@2024-10-01' = {
  parent: openai
  dependsOn: [
    embeddingDeployment
  ]
  name: 'gpt-4.1-mini'
  sku: {
    name: 'DataZoneStandard'
    capacity: 10
  }
  properties: {
    model: {
      format: 'OpenAI'
      name: 'gpt-4.1-mini'
      version: '2025-04-14'
    }
  }
}

resource embeddingDeployment 'Microsoft.CognitiveServices/accounts/deployments@2024-10-01' = {
  parent: openai
  name: 'text-embedding-3-small'
  sku: {
    name: 'DataZoneStandard'
    capacity: 10
  }
  properties: {
    model: {
      format: 'OpenAI'
      name: 'text-embedding-3-small'
      version: '1'
    }
  }
}

resource storage 'Microsoft.Storage/storageAccounts@2023-05-01' = {
  name: 'stdocpilotprod'
  location: location
  tags: tags
  sku: {
    name: 'Standard_LRS'
  }
  kind: 'StorageV2'
  properties: {
    accessTier: 'Hot'
    allowBlobPublicAccess: false
    allowSharedKeyAccess: false
    minimumTlsVersion: 'TLS1_2'
    supportsHttpsTrafficOnly: true
    publicNetworkAccess: 'Enabled'
  }
}

resource blobService 'Microsoft.Storage/storageAccounts/blobServices@2023-05-01' = {
  parent: storage
  name: 'default'
}

resource sources 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' = {
  parent: blobService
  name: 'sources'
  properties: {
    publicAccess: 'None'
  }
}

resource keyVault 'Microsoft.KeyVault/vaults@2023-07-01' = {
  name: 'kv-docpilot-prod'
  location: location
  tags: tags
  properties: {
    tenantId: tenant().tenantId
    sku: {
      family: 'A'
      name: 'standard'
    }
    enableRbacAuthorization: true
    enableSoftDelete: true
    softDeleteRetentionInDays: 7
    publicNetworkAccess: 'Enabled'
  }
}

var lawKeys = law.listKeys()

resource containerEnv 'Microsoft.App/managedEnvironments@2024-03-01' = {
  name: 'cae-docpilot-prod'
  location: location
  tags: tags
  properties: {
    appLogsConfiguration: {
      destination: 'log-analytics'
      logAnalyticsConfiguration: {
        customerId: law.properties.customerId
        sharedKey: lawKeys.primarySharedKey
      }
    }
    workloadProfiles: [
      {
        name: 'Consumption'
        workloadProfileType: 'Consumption'
      }
    ]
  }
}

resource containerApp 'Microsoft.App/containerApps@2024-03-01' = {
  name: 'ca-docpilot-prod'
  location: location
  tags: tags
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    managedEnvironmentId: containerEnv.id
    configuration: {
      activeRevisionsMode: 'Single'
      ingress: union({
        external: true
        targetPort: targetPort
        transport: 'auto'
        allowInsecure: false
      }, empty(customDomainCertificateId) ? {} : {
        customDomains: [
          {
            name: customDomainName
            bindingType: 'SniEnabled'
            certificateId: customDomainCertificateId
          }
        ]
      })
      secrets: [
        {
          name: 'appinsights-connection-string'
          value: appInsights.properties.ConnectionString
        }
      ]
    }
    template: {
      containers: [
        {
          name: 'api'
          image: containerImage
          resources: {
            cpu: json('0.5')
            memory: '1Gi'
          }
          env: [
            {
              name: 'APPLICATIONINSIGHTS_CONNECTION_STRING'
              secretRef: 'appinsights-connection-string'
            }
            {
              name: 'AZURE_STORAGE_ACCOUNT_URL'
              value: 'https://${storage.name}.blob.core.windows.net'
            }
            {
              name: 'AZURE_STORAGE_CONTAINER'
              value: sources.name
            }
            {
              name: 'AZURE_OPENAI_ENDPOINT'
              value: openai.properties.endpoint
            }
            {
              name: 'AZURE_OPENAI_EMBEDDING_DEPLOYMENT'
              value: embeddingDeployment.name
            }
            {
              name: 'AZURE_SEARCH_ENDPOINT'
              value: 'https://${search.name}.search.windows.net'
            }
            {
              name: 'AZURE_SEARCH_INDEX'
              value: 'docpilot'
            }
            {
              name: 'DOCPILOT_COOKIE_SECURE'
              value: 'true'
            }
          ]
          probes: [
            {
              type: 'Liveness'
              httpGet: {
                path: healthPath
                port: targetPort
              }
              initialDelaySeconds: 5
              periodSeconds: 30
            }
          ]
        }
      ]
      scale: {
        minReplicas: 0
        maxReplicas: 2
      }
    }
  }
}

var openAiUserRole = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '5e0bd9bd-7b93-4f28-af87-19fc36ad61bd')
var searchDataRole = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '8ebe5a00-799e-43f5-93ac-243d3dce84a7')
var keyVaultSecretsRole = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '4633458b-17de-408a-b874-0445c86b69e6')
var blobDataRole = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', 'ba92f5b4-2d11-453d-a403-e96b0029c9fe')

resource openAiRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(openai.id, containerApp.id, openAiUserRole)
  scope: openai
  properties: {
    roleDefinitionId: openAiUserRole
    principalId: containerApp.identity.principalId
    principalType: 'ServicePrincipal'
  }
}

resource searchRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(search.id, containerApp.id, searchDataRole)
  scope: search
  properties: {
    roleDefinitionId: searchDataRole
    principalId: containerApp.identity.principalId
    principalType: 'ServicePrincipal'
  }
}

resource keyVaultRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(keyVault.id, containerApp.id, keyVaultSecretsRole)
  scope: keyVault
  properties: {
    roleDefinitionId: keyVaultSecretsRole
    principalId: containerApp.identity.principalId
    principalType: 'ServicePrincipal'
  }
}

resource blobRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(storage.id, containerApp.id, blobDataRole)
  scope: storage
  properties: {
    roleDefinitionId: blobDataRole
    principalId: containerApp.identity.principalId
    principalType: 'ServicePrincipal'
  }
}

resource budget 'Microsoft.Consumption/budgets@2023-11-01' = {
  name: 'budget-docpilot-10'
  properties: {
    category: 'Cost'
    amount: 10
    timeGrain: 'Monthly'
    timePeriod: {
      startDate: '2026-10-01'
    }
    notifications: {
      actual50: {
        enabled: true
        operator: 'GreaterThan'
        threshold: 50
        contactEmails: [
          budgetContactEmail
        ]
        thresholdType: 'Actual'
      }
      actual100: {
        enabled: true
        operator: 'GreaterThan'
        threshold: 100
        contactEmails: [
          budgetContactEmail
        ]
        thresholdType: 'Actual'
      }
    }
  }
}

output containerAppFqdn string = containerApp.properties.configuration.ingress.fqdn
output customDomainVerificationId string = containerEnv.properties.customDomainConfiguration.customDomainVerificationId
output openAiEndpoint string = openai.properties.endpoint
output searchEndpoint string = 'https://${search.name}.search.windows.net'
output keyVaultName string = keyVault.name
output storageAccountName string = storage.name
output storageBlobEndpoint string = 'https://${storage.name}.blob.core.windows.net'
output storageContainerName string = sources.name
output containerAppPrincipalId string = containerApp.identity.principalId
output chatDeploymentName string = chatDeployment.name
output embeddingDeploymentName string = embeddingDeployment.name
