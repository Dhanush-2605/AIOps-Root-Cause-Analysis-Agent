variable "subscription_id" {
  description = "Azure subscription ID"
  type        = string
}

variable "tenant_id" {
  description = "Azure AD (Entra ID) tenant ID"
  type        = string
}

variable "location" {
  description = "Azure region (equivalent to GCP var.region)"
  type        = string
  default     = "centralindia"
  
}

variable "resource_group_name" {
  description = "Resource group that holds everything (GCP has no direct equivalent — a project is the closest analog)"
  type        = string
  default     = "rg-production-1"
}

variable "db_root_password" {
  description = "Database root password, stored in Key Vault"
  type        = string
  sensitive   = true
}

variable "github_repo" {
  description = "GitHub repo allowed to assume the pipeline identity, format: org/repo"
  type        = string
}

variable "groq_api_key" {
  description = "Groq API key used by the RCA agent; stored in the app-env Key Vault secret"
  type        = string
  sensitive   = true
}

variable "github_token" {
  description = "GitHub fine-grained token for this repo (Contents: read/write, Variables: read/write). Used by the agent to commit post-mortems and by Terraform to set Actions variables."
  type        = string
  sensitive   = true
}

variable "pg_repl_password" {
  description = "Postgres replication password; stored as the pg-repl-password Key Vault secret"
  type        = string
  sensitive   = true
}

variable "pgpool_admin_password" {
  description = "pgpool admin password; stored as the pgpool-admin-password Key Vault secret"
  type        = string
  sensitive   = true
}


