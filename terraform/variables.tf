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
  default     = "rg-production"
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

# variable "gemini_api_key" {
#   description = "API key for the AI SRE agent (kept commented, matching source)"
#   type        = string
#   sensitive   = true
# }
