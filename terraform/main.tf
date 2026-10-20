# ==========================================================================
# 1. RESOURCE GROUP
# ==========================================================================

resource "azurerm_resource_group" "main" {
  name     = var.resource_group_name
  location = var.location
}

# ==========================================================================
# 2. AKS CLUSTER footprint 
# ==========================================================================


resource "azurerm_kubernetes_cluster" "primary" {
  name                = "aks-production-cluster"
  location            = azurerm_resource_group.main.location
  resource_group_name = azurerm_resource_group.main.name
  dns_prefix          = "aksprod"

  oidc_issuer_enabled       = true
  workload_identity_enabled = true
key_vault_secrets_provider {
    secret_rotation_enabled = true
  }
  default_node_pool {
    name                 = "system"
    vm_size              = "Standard_D2s_v5"
    auto_scaling_enabled = true
    min_count            = 1
    max_count            = 3
  }

  node_provisioning_profile {
      mode = "Manual"
  }

  identity {
    type = "SystemAssigned"
  }
}

# ==========================================================================
# 3. SECRET CONFIGURATION LAYER (Azure Key Vault)
# ==========================================================================

data "azurerm_client_config" "current" {}

resource "azurerm_key_vault" "main" {
  name                       = "kv-doom-secrets2" # must be globally unique, <=24 chars
  location                   = azurerm_resource_group.main.location
  resource_group_name        = azurerm_resource_group.main.name
  tenant_id                  = var.tenant_id
  sku_name                   = "standard"
  rbac_authorization_enabled  = true # access grants live in access.tf
  purge_protection_enabled   = false
}

resource "azurerm_key_vault_secret" "db_password" {
  name         = "db-password"
  value        = var.db_root_password
  key_vault_id = azurerm_key_vault.main.id

  depends_on = [time_sleep.kv_rbac_propagation]
}

# ==========================================================================
# 4. CONTAINER REGISTRY (Azure Container Registry)
# ==========================================================================

resource "azurerm_container_registry" "app_repo" {
  name                = "acrnodejsapprepo" # must be globally unique, alphanumeric only, no dashes
  resource_group_name = azurerm_resource_group.main.name
  location            = azurerm_resource_group.main.location
  sku                 = "Standard"
  admin_enabled       = false
}
