


# ==========================================================================
# 1. PLATFORM SERVICES & APIS
# ==========================================================================

resource "google_project_service" "container" {
  service            = "container.googleapis.com"
  disable_on_destroy = false
}

resource "google_project_service" "secretmanager" {
  service            = "secretmanager.googleapis.com"
  disable_on_destroy = false
}

# ==========================================================================
# 2. GKE AUTOPILOT CLUSTER footprint
# ==========================================================================

resource "google_container_cluster" "primary" {
  name             = "gke-production-cluster"
  location         = var.region
  enable_autopilot = true
  
  secret_manager_config {
    enabled = true
  }

  depends_on = [google_project_service.container]
}

# ==========================================================================
# 3. SECRET CONFIGURATION LAYER (GCP Secret Manager)
# ==========================================================================

resource "google_secret_manager_secret" "db_password" {
  secret_id = "db-password"
  replication {
    auto {}
  }
  depends_on = [google_project_service.secretmanager]
}

resource "google_secret_manager_secret_version" "db_password_version" {
  secret      = google_secret_manager_secret.db_password.id
  secret_data = var.db_root_password
}



# ==========================================================================
# 4. CONTAINER REGISTRY (Artifact Registry)
# ==========================================================================

# Enable the Artifact Registry API
resource "google_project_service" "artifactregistry" {
  service            = "artifactregistry.googleapis.com"
  disable_on_destroy = false
}

# Create the Docker Repository
resource "google_artifact_registry_repository" "app_repo" {
  location      = var.region
  repository_id = "nodejs-app-repo"
  description   = "Private Docker repository for frontend and backend images"
  format        = "DOCKER"
  
  depends_on = [google_project_service.artifactregistry]
}



# ==========================================================================
# 5. RUNTIME WORKLOAD IDENTITY BRIDGE (Pod to GCP Vault)
# ==========================================================================

# The Google-side execution identity
resource "google_service_account" "backend_gsa" {
  account_id   = "backend-db-accessor"
  display_name = "Pod Runtime Identity for Database Access"
}

# Grant the Google Identity rights to read from the vault slot
resource "google_secret_manager_secret_iam_member" "backend_secret_access" {
  secret_id = google_secret_manager_secret.db_password.id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.backend_gsa.email}"
}

# The cryptographic binding linking the Kubernetes Pod identifier to the Google identity
resource "google_service_account_iam_member" "pod_workload_identity_binding" {
  service_account_id = google_service_account.backend_gsa.name
  role               = "roles/iam.workloadIdentityUser"
  member             = "serviceAccount:${var.project_id}.svc.id.goog[default/backend-sa]"
}