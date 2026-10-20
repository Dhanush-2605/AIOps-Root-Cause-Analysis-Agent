# ==========================================================================
# ACCESS GRANTS THAT USED TO BE MANUAL STEPS
# ==========================================================================

# AKS nodes pull images from ACR (without this, pods end up in ImagePullBackOff)
resource "azurerm_role_assignment" "aks_acr_pull" {
  scope                            = azurerm_container_registry.app_repo.id
  role_definition_name             = "AcrPull"
  principal_id                     = azurerm_kubernetes_cluster.primary.kubelet_identity[0].object_id
  skip_service_principal_aad_check = true
}

# The Key Vault CSI add-on identity reads secrets for the backend and agent pods
# (SecretProviderClass "azure-kv-provider" authenticates with this identity)
resource "azurerm_role_assignment" "csi_addon_secret_access" {
  scope                            = azurerm_key_vault.main.id
  role_definition_name             = "Key Vault Secrets User"
  principal_id                     = azurerm_kubernetes_cluster.primary.key_vault_secrets_provider[0].secret_identity[0].object_id
  skip_service_principal_aad_check = true
}
