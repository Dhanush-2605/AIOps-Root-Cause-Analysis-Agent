# 1. Enable IAM Credentials API
resource "google_project_service" "iamcredentials" {
  service            = "iamcredentials.googleapis.com"
  disable_on_destroy = false
}

# 2. Create the Google Service Account that the GitHub Pipeline will assume
resource "google_service_account" "github_actions_sa" {
  account_id   = "github-actions-pipeline"
  display_name = "GitHub Actions CI/CD Pipeline Account"
}

# 3. Grant the Pipeline SA administrative control over GKE, Secrets, and Compute
resource "google_project_iam_member" "pipeline_roles" {
  for_each = toset([
    "roles/container.admin",       # Allows full management of GKE clusters
    "roles/secretmanager.admin",   # Allows managing secrets in Secret Manager
    "roles/compute.admin",         # Allows managing GKE network footprints
    "roles/iam.serviceAccountUser", # Allows assigning service accounts to GKE nodes
    "roles/artifactregistry.writer" # Allows GitHub to push docker images!
  ])
  project = var.project_id
  role    = each.value
  member  = "serviceAccount:${google_service_account.github_actions_sa.email}"
}

# 4. Create a Workload Identity Pool for GitHub
resource "google_iam_workload_identity_pool" "github_pool" {
  workload_identity_pool_id = "github-actions-pool"
  display_name              = "GitHub Actions Pool"
}

# 5. Create the OIDC Provider connecting GitHub to GCP
resource "google_iam_workload_identity_pool_provider" "github_provider" {
  workload_identity_pool_id          = google_iam_workload_identity_pool.github_pool.workload_identity_pool_id
  workload_identity_pool_provider_id = "github-provider"
  attribute_mapping = {
    "google.subject"       = "assertion.sub"
    "attribute.repository" = "assertion.repository"
  }

  # This tells the bouncer: "If the raw token doesn't belong to our repository, 
  # don't even bother translating it. Drop it immediately."
  attribute_condition = "assertion.repository == '${var.github_repo}'"
  oidc {
    issuer_uri = "https://token.actions.githubusercontent.com"
  }
}

# 6. Allow your specific GitHub Repository to assume the Pipeline GSA

resource "google_service_account_iam_member" "github_sa_binding" {
  # The specific costume (GSA) being requested
  service_account_id = google_service_account.github_actions_sa.name
  
  # The specific permission that allows token exchange (impersonation)
  role               = "roles/iam.workloadIdentityUser" 
  
  # The strict rule: Only grant this if the incoming mapped token exactly matches your repo
member             = "principalSet://iam.googleapis.com/${google_iam_workload_identity_pool.github_pool.name}/attribute.repository/${var.github_repo}"

}