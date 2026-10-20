# ==========================================================================
# GITHUB ACTIONS VARIABLES
# ==========================================================================
# The deploy workflow reads these as ${{ vars.NAME }}, so no IDs are hard-coded


locals {
  github_actions_variables = {
    AZURE_CLIENT_ID        = azurerm_user_assigned_identity.github_actions_identity.client_id
    AZURE_TENANT_ID        = var.tenant_id
    AZURE_SUBSCRIPTION_ID  = var.subscription_id
    RESOURCE_GROUP         = azurerm_resource_group.main.name
    CLUSTER_NAME           = azurerm_kubernetes_cluster.primary.name
    ACR_NAME               = azurerm_container_registry.app_repo.name
    ACR_LOGIN_SERVER       = azurerm_container_registry.app_repo.login_server
    KEYVAULT_NAME          = azurerm_key_vault.main.name
    BACKEND_CLIENT_ID      = azurerm_user_assigned_identity.backend_identity.client_id
    CSI_IDENTITY_CLIENT_ID = azurerm_kubernetes_cluster.primary.key_vault_secrets_provider[0].secret_identity[0].client_id
  }
}

resource "github_actions_variable" "azure" {
  for_each      = local.github_actions_variables
  repository    = split("/", var.github_repo)[1]
  variable_name = each.key
  value         = each.value
}
