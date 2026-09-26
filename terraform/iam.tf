# ==========================================================================
# BACKEND POD → KEY VAULT WORKLOAD IDENTITY
# ==========================================================================

# The Azure-side execution identity (equivalent to google_service_account)
resource "azurerm_user_assigned_identity" "backend_identity" {
  name                = "backend-db-accessor"
  resource_group_name = azurerm_resource_group.main.name
  location            = azurerm_resource_group.main.location
}

# Grant the identity rights to read secrets from the vault (RBAC role, not an IAM policy binding)
resource "azurerm_role_assignment" "backend_secret_access" {
  scope                = azurerm_key_vault.main.id
  role_definition_name = "Key Vault Secrets User"
  principal_id         = azurerm_user_assigned_identity.backend_identity.principal_id
}

# The cryptographic binding linking the Kubernetes ServiceAccount to the Azure identity
# (equivalent to google_service_account_iam_member workload identity binding)
resource "azurerm_federated_identity_credential" "pod_workload_identity_binding" {
  name                = "backend-pod-federation"
  resource_group_name = azurerm_resource_group.main.name
  audience            = ["api://AzureADTokenExchange"]
  issuer              = azurerm_kubernetes_cluster.primary.oidc_issuer_url
  parent_id           = azurerm_user_assigned_identity.backend_identity.id
  subject             = "system:serviceaccount:default:backend-sa"
}

# ==========================================================================
# GITHUB ACTIONS CI/CD PIPELINE IDENTITY
# ==========================================================================

# 1. The Azure identity that the GitHub Pipeline will assume
#    (equivalent to google_service_account.github_actions_sa)
resource "azurerm_user_assigned_identity" "github_actions_identity" {
  name                = "github-actions-pipeline"
  resource_group_name = azurerm_resource_group.main.name
  location            = azurerm_resource_group.main.location
}

# 2. Grant the Pipeline identity control over AKS, Key Vault, networking, and ACR
#    (role scope is the resource group — the closest equivalent to project-level IAM in GCP)
resource "azurerm_role_assignment" "pipeline_roles" {
  for_each = toset([
    "Azure Kubernetes Service Cluster Admin Role", # full management of AKS clusters
    "Key Vault Administrator",                     # manage secrets in Key Vault
    "Network Contributor",                         # manage network footprints
    "Managed Identity Operator",                   # assign managed identities to AKS nodes
    "AcrPush",                                      # allows GitHub to push docker images
  ])
  scope                = azurerm_resource_group.main.id
  role_definition_name = each.value
  principal_id         = azurerm_user_assigned_identity.github_actions_identity.principal_id
}

# 3. Federated credential connecting GitHub's OIDC token to the Azure identity
#    (equivalent to the Workload Identity Pool + Provider + binding trio in GCP)
resource "azurerm_federated_identity_credential" "github_sa_binding" {
  name                = "github-actions-federation"
  resource_group_name = azurerm_resource_group.main.name
  audience            = ["api://AzureADTokenExchange"]
  issuer              = "https://token.actions.githubusercontent.com"
  parent_id           = azurerm_user_assigned_identity.github_actions_identity.id

  # Restrict to your specific repo + branch — adjust the ref, or use
  # "repo:${var.github_repo}:pull_request" / ":environment:prod" for other trigger types
  subject = "repo:${var.github_repo}:ref:refs/heads/main"
}

# ==========================================================================
# AI SRE AGENT SECRETS & IDENTITIES (commented out, matching source)
# ==========================================================================

# # Key Vault secret for the Gemini API key
# resource "azurerm_key_vault_secret" "gemini_api_key" {
#   name         = "gemini-api-key"
#   value        = var.gemini_api_key
#   key_vault_id = azurerm_key_vault.main.id
# }

# # Dedicated managed identity for the CrewAI pod
# resource "azurerm_user_assigned_identity" "ai_agent_identity" {
#   name                = "ai-sre-agent"
#   resource_group_name = azurerm_resource_group.main.name
#   location            = azurerm_resource_group.main.location
# }

# # Strict RBAC: allow the AI agent to read only the Gemini key's vault
# resource "azurerm_role_assignment" "ai_agent_secret_access" {
#   scope                = azurerm_key_vault.main.id
#   role_definition_name = "Key Vault Secrets User"
#   principal_id         = azurerm_user_assigned_identity.ai_agent_identity.principal_id
# }

# # Workload identity bridge: connects AKS to Azure for the AI Agent
# resource "azurerm_federated_identity_credential" "ai_agent_workload_identity" {
#   name                = "ai-agent-federation"
#   resource_group_name = azurerm_resource_group.main.name
#   audience            = ["api://AzureADTokenExchange"]
#   issuer              = azurerm_kubernetes_cluster.primary.oidc_issuer_url
#   parent_id           = azurerm_user_assigned_identity.ai_agent_identity.id
#   subject             = "system:serviceaccount:default:ai-agent-k8s-sa"
# }
