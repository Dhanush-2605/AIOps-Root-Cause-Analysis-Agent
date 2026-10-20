# ==========================================================================
# KEY VAULT SECRETS READ BY THE WORKLOADS
# ==========================================================================

# Key Vault uses RBAC, so a fresh role assignment needs a moment to propagate
# before Terraform can write secrets with it.
resource "time_sleep" "kv_rbac_propagation" {
  create_duration = "90s"
  depends_on      = [azurerm_role_assignment.tf_executor_kv_access]
}

# One dotenv-style file read by the backend, agent, postgres and pgpool.
# Keys: DB_PASSWORD, GROQ_API_KEY, GITHUB_TOKEN, GITHUB_REPO
resource "azurerm_key_vault_secret" "app_env" {
  name         = "app-env"
  key_vault_id = azurerm_key_vault.main.id
  value = join("\n", [
    "DB_PASSWORD=${var.db_root_password}",
    "GROQ_API_KEY=${var.groq_api_key}",
    "GITHUB_TOKEN=${var.github_token}",
    "GITHUB_REPO=${var.github_repo}",
  ])

  depends_on = [time_sleep.kv_rbac_propagation]
}

resource "azurerm_key_vault_secret" "pg_repl_password" {
  name         = "pg-repl-password"
  value        = var.pg_repl_password
  key_vault_id = azurerm_key_vault.main.id

  depends_on = [time_sleep.kv_rbac_propagation]
}

resource "azurerm_key_vault_secret" "pgpool_admin_password" {
  name         = "pgpool-admin-password"
  value        = var.pgpool_admin_password
  key_vault_id = azurerm_key_vault.main.id

  depends_on = [time_sleep.kv_rbac_propagation]
}
