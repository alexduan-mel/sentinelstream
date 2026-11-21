variable "project" {
  description = "Name used for resources and the Project tag."
  type        = string
  default     = "sentinelstream"
}

variable "region" {
  description = "AWS region for every stage-1 resource."
  type        = string
  default     = "us-east-1"
}

variable "alert_email" {
  description = "Email address for budget and alarm notifications. Set it in terraform.tfvars (gitignored), never in git."
  type        = string
  sensitive   = true
}

variable "github_oidc_subject_prefix" {
  description = <<-EOT
    GitHub OIDC `sub` prefix for this repository. The repo uses immutable subjects, so it is
    `repo:<owner>@<owner_id>/<repo>@<repo_id>`. Check with:
    gh api repos/<owner>/<repo>/actions/oidc/customization/sub
  EOT
  type        = string
  default     = "repo:alexduan-mel@7532736/sentinelstream@1465008853"
}

variable "deploy_branch" {
  description = "Only workflows running on this branch can assume the deploy role."
  type        = string
  default     = "main"
}

variable "instance_type" {
  description = "EC2 instance type. Must be arm64 (Graviton) to match the image. Move up to t4g.small (2 GiB) if memory gets tight."
  type        = string
  default     = "t4g.micro"
}

variable "root_volume_gb" {
  description = "Root EBS volume size: OS, Docker images and container logs."
  type        = number
  default     = 12
}

variable "data_volume_gb" {
  description = "Separate EBS volume for Postgres data, kept across instance replacement."
  type        = number
  default     = 10
}

variable "log_retention_days" {
  description = "CloudWatch Logs retention."
  type        = number
  default     = 14
}

variable "monthly_budget_usd" {
  description = "AWS Budgets monthly cost limit."
  type        = number
  default     = 30
}

variable "budget_early_warning_usd" {
  description = "Actual spend that triggers an early warning email."
  type        = number
  default     = 10
}

variable "snapshot_retention_days" {
  description = "Daily snapshots of the data volume to keep."
  type        = number
  default     = 7
}
