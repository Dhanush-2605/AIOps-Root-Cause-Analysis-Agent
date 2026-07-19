variable "project_id" {
  description = "The absolute Google Cloud Project ID."
  type        = string
}

variable "region" {
  description = "The target deployment region."
  type        = string
  default     = "us-central1"
}

variable "github_repo" {
  description = "The GitHub repository formatted as username/repo."
  type        = string
}

variable "db_root_password" {
  description = "The application database password injected into Secret Manager."
  type        = string
  sensitive   = true
}


variable "gemini_api_key" {

  type        = string
  default="temp"
}