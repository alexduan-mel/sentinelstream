# GitHub Actions → AWS with OIDC: no stored AWS keys.
#
# The OIDC provider was created by hand for the first smoke test, and an account can only
# have one per URL, so it is imported. The deploy role is new; after apply, point the
# AWS_ROLE_ARN repo secret at it and delete the hand-made role.

import {
  to = aws_iam_openid_connect_provider.github
  id = "arn:${local.partition}:iam::${local.account_id}:oidc-provider/token.actions.githubusercontent.com"
}

resource "aws_iam_openid_connect_provider" "github" {
  url            = "https://token.actions.githubusercontent.com"
  client_id_list = ["sts.amazonaws.com"]
}

resource "aws_iam_role" "github_deploy" {
  name                 = "${var.project}-github-deploy"
  description          = "Assumed by GitHub Actions on ${var.deploy_branch} to push images and deploy"
  max_session_duration = 3600
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Federated = aws_iam_openid_connect_provider.github.arn }
      Action    = "sts:AssumeRoleWithWebIdentity"
      Condition = {
        StringEquals = {
          "token.actions.githubusercontent.com:aud" = "sts.amazonaws.com"
          "token.actions.githubusercontent.com:sub" = "${var.github_oidc_subject_prefix}:ref:refs/heads/${var.deploy_branch}"
        }
      }
    }]
  })
}

# Push images, run the deploy command on the app instance, and read the result.
# It cannot change infrastructure: Terraform is applied by the owner, not CI.
resource "aws_iam_role_policy" "github_deploy" {
  name = "${var.project}-github-deploy"
  role = aws_iam_role.github_deploy.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "EcrAuth"
        Effect   = "Allow"
        Action   = "ecr:GetAuthorizationToken"
        Resource = "*"
      },
      {
        Sid    = "EcrPush"
        Effect = "Allow"
        Action = [
          "ecr:BatchCheckLayerAvailability",
          "ecr:BatchGetImage",
          "ecr:CompleteLayerUpload",
          "ecr:DescribeImages",
          "ecr:GetDownloadUrlForLayer",
          "ecr:InitiateLayerUpload",
          "ecr:PutImage",
          "ecr:UploadLayerPart",
        ]
        Resource = aws_ecr_repository.app.arn
      },
      {
        Sid      = "FindInstance"
        Effect   = "Allow"
        Action   = "ec2:DescribeInstances"
        Resource = "*"
      },
      {
        Sid      = "RunDeployOnAppInstance"
        Effect   = "Allow"
        Action   = "ssm:SendCommand"
        Resource = "arn:${local.partition}:ec2:${var.region}:${local.account_id}:instance/*"
        Condition = {
          StringEquals = { "aws:ResourceTag/Project" = var.project }
        }
      },
      {
        Sid      = "RunShellScriptDocument"
        Effect   = "Allow"
        Action   = "ssm:SendCommand"
        Resource = "arn:${local.partition}:ssm:${var.region}::document/AWS-RunShellScript"
      },
      {
        Sid      = "ReadCommandResult"
        Effect   = "Allow"
        Action   = ["ssm:GetCommandInvocation", "ssm:ListCommandInvocations"]
        Resource = "*"
      },
    ]
  })
}
