output "workload_identity_provider" {
  description = "The value to use for 'workload_identity_provider' in your GitHub Actions workflow step."
  value       = google_iam_workload_identity_pool_provider.github_provider.name
}

output "pipeline_service_account_email" {
  description = "The email address of the pipeline service account to place in your GitHub workflow."
  value       = google_service_account.github_actions_sa.email
}