terraform {
  required_version = ">= 1.5.0"
  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = ">= 3.90.0"
    }
    github = {
      source  = "integrations/github"
      version = "~> 6.0"
    }
    time = {
      source  = "hashicorp/time"
      version = ">= 0.9.0"
    }
  }
}

provider "azurerm" {

  features {}
  subscription_id = var.subscription_id
  tenant_id       = var.tenant_id
}

# Writes the GitHub Actions variables the deploy workflow reads (see github.tf)
provider "github" {
  owner = split("/", var.github_repo)[0]
  token = var.github_token
}
