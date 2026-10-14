using 'main.bicep'

param location = 'eastus2'
param searchLocation = 'eastus'
param budgetContactEmail = 'hmeyer@nitrosoft.net'
param containerImage = 'mcr.microsoft.com/k8se/quickstart:latest'
param targetPort = 80
param healthPath = '/'
