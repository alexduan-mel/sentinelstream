output "github_deploy_role_arn" {
  description = "Set as the AWS_ROLE_ARN repository secret."
  value       = aws_iam_role.github_deploy.arn
}

output "ecr_repository_url" {
  value = aws_ecr_repository.app.repository_url
}

output "instance_id" {
  description = "For SSM sessions: aws ssm start-session --target <id>"
  value       = aws_instance.app.id
}

output "data_bucket" {
  value = aws_s3_bucket.data.bucket
}

output "log_group" {
  value = aws_cloudwatch_log_group.app.name
}
