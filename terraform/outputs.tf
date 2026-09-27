output "workload_identity_client_id" {
  description = "Use as 'client-id' / AZURE_CLIENT_ID in the azure/login GitHub Actions step."
  value       = azurerm_user_assigned_identity.github_actions_identity.client_id
}

output "tenant_id" {
  description = "Use as 'tenant-id' / AZURE_TENANT_ID in the azure/login GitHub Actions step."
  value       = var.tenant_id
}

output "subscription_id" {
  description = "Use as 'subscription-id' / AZURE_SUBSCRIPTION_ID in the azure/login GitHub Actions step."
  value       = var.subscription_id
}

output "acr_login_server" {
  description = "Registry hostname to push/pull images, e.g. in your CI docker build/push step."
  value       = azurerm_container_registry.app_repo.login_server
}

output "aks_cluster_name" {
  value = azurerm_kubernetes_cluster.primary.name
}
