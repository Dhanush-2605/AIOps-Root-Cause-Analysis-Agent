terraform {
  required_version = ">= 1.5.0"
  required_providers {
    google = {
      source  = "hashicorp/google"
      # Update this line to fetch the newer provider that supports Secret Manager natively
      version = ">= 6.7.0" 
    }
  }
}

provider "google" {
  project = var.project_id
  region  = var.region
}