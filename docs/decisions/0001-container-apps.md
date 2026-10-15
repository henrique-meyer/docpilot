# Container Apps

DocPilot runs as one container on Container Apps Consumption, and it scales to zero. A virtual machine or App Service plan would keep billing while the demo is idle. AKS can run the same image later; v1 does not need a cluster.

Images are published to GitHub Container Registry. The app pulls a public image with its managed identity for Azure data, and it does not need a registry password. East US 2 is the default region. Azure AI Search is in East US because East US 2 had no free-tier capacity.
