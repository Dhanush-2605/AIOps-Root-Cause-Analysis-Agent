# ==========================================================================
# 1. RESOURCE GROUP
# ==========================================================================
# GCP has no direct equivalent to this — a resource group is Azure's
# container for everything below. Your GCP project_id maps loosely to this.

resource "azurerm_resource_group" "main" {
  name     = var.resource_group_name
  location = var.location
}

# ==========================================================================
# 2. AKS CLUSTER footprint (equivalent to your GKE Autopilot cluster)
# ==========================================================================
# NOTE: Azure has no exact 1:1 match for GKE Autopilot's fully-managed,
# pay-per-pod model. "AKS Automatic" is the closest match if your azurerm
# provider version supports it. Below is a standard AKS cluster with
# node-pool autoscaling, which is the safe, broadly-supported equivalent —
# swap in aks_automatic settings later if you want to get closer to Autopilot.

resource "azurerm_kubernetes_cluster" "primary" {
  name                = "aks-production-cluster"
  location            = azurerm_resource_group.main.location
  resource_group_name = azurerm_resource_group.main.name
  dns_prefix          = "aksprod"

  # Required so pods can federate with Azure managed identities (workload identity) —
  # actual identity bindings live in access.tf
  oidc_issuer_enabled       = true
  workload_identity_enabled = true

  default_node_pool {
    name                 = "system"
    vm_size              = "Standard_D2s_v5"
    auto_scaling_enabled = true
    min_count            = 1
    max_count            = 3
  }

  identity {
    type = "SystemAssigned"
  }
}

# ==========================================================================
# 3. SECRET CONFIGURATION LAYER (Azure Key Vault = GCP Secret Manager)
# ==========================================================================

data "azurerm_client_config" "current" {}

resource "azurerm_key_vault" "main" {
  name                       = "kv-prod-secrets" # must be globally unique, <=24 chars
  location                   = azurerm_resource_group.main.location
  resource_group_name        = azurerm_resource_group.main.name
  tenant_id                  = var.tenant_id
  sku_name                   = "standard"
  enable_rbac_authorization  = true # access grants live in access.tf
  purge_protection_enabled   = false
}

resource "azurerm_key_vault_secret" "db_password" {
  name         = "db-password"
  value        = var.db_root_password
  key_vault_id = azurerm_key_vault.main.id
}

# ==========================================================================
# 4. CONTAINER REGISTRY (Azure Container Registry = GCP Artifact Registry)
# ==========================================================================

resource "azurerm_container_registry" "app_repo" {
  name                = "acrnodejsapprepo" # must be globally unique, alphanumeric only, no dashes
  resource_group_name = azurerm_resource_group.main.name
  location            = azurerm_resource_group.main.location
  sku                 = "Standard"
  admin_enabled       = false
}
