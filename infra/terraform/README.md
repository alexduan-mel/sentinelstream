# Terraform: AWS stage 1

Everything SentinelStream runs on in AWS (us-east-1). See [ADR 0001](../../docs/decisions/0001-stage1-single-ec2.md) for why it is one EC2 instance.

| File | What it creates |
| --- | --- |
| `bootstrap/` | S3 bucket for Terraform state (applied once, local state) |
| `stage1/network.tf` | VPC, one public subnet, internet gateway, a security group with **no inbound rules** |
| `stage1/compute.tf` | EC2 `t4g.micro` (AL2023 arm64, IMDSv2), a separate encrypted EBS volume for Postgres data with 7 daily snapshots |
| `stage1/iam_instance.tf` | Instance role: SSM, ECR pull, read `/sentinelstream/*` parameters, write its logs, use its bucket |
| `stage1/github_oidc.tf` | GitHub OIDC provider (imported) and the deploy role, which only `main` can assume |
| `stage1/ecr.tf` | Image repository with immutable SHA tags; the last 30 images are kept for rollback |
| `stage1/storage.tf` | S3 bucket for the filing archive and DB backups (versioned, private, TLS only) |
| `stage1/observability.tf` | Log group (14 days), failed-task-run alarm, SNS email topic |
| `stage1/budget.tf` | Monthly budget on spend before credits: email at $15 actual, $30 forecast and $30 actual |

Expected cost is about US$12 a month: the instance $6.13, public IPv4 $3.65, 22 GB of gp3 $1.76, and snapshots, logs, ECR and S3 under $1 together.

To give Postgres more memory later, set `instance_type = "t4g.small"` (2 GiB, about US$19 a month in total) and apply. The instance stops and starts in place, and no data is lost.

## Who applies it

The owner applies Terraform from their own machine. CI only runs `fmt` and `validate`, and its deploy role can push images and run the deploy command but can't change infrastructure.

## First-time setup

Prerequisites: Terraform ≥ 1.10, the AWS CLI v2, the [Session Manager plugin](https://docs.aws.amazon.com/systems-manager/latest/userguide/session-manager-working-with-install-plugin.html), and an admin profile (IAM Identity Center recommended, not root keys).

```sh
export AWS_PROFILE=<your-admin-profile>

# 1. State bucket (once)
terraform -chdir=infra/terraform/bootstrap init
terraform -chdir=infra/terraform/bootstrap apply
# → prints state_bucket

# 2. Stage 1
cd infra/terraform/stage1
cp backend.hcl.example backend.hcl            # put the state_bucket name in it (gitignored)
cp terraform.tfvars.example terraform.tfvars  # put your alert email in it (gitignored)
terraform init -backend-config=backend.hcl
terraform plan -out=stage1.tfplan
terraform apply stage1.tfplan
```

After the first apply:

1. Click the confirmation link AWS SNS emails you, or no alarm or budget email arrives.
2. Point CI at the new deploy role, then check it with the OIDC smoke test:
   ```sh
   gh secret set AWS_ROLE_ARN --body "$(terraform output -raw github_deploy_role_arn)"
   gh workflow run aws-test.yml --ref main
   ```
3. Once that passes, delete the hand-made role from the first OIDC test in the IAM console. The OIDC provider is now managed here.

## Secrets

Secrets live in SSM Parameter Store under `/sentinelstream/`, never in Terraform (state would hold them in plain text). The deploy script reads them into the containers' environment at start.

The deploy refuses to start without the four Postgres settings. Set them once (pick a long random password; it is used when Postgres first initialises its data directory on the EBS volume, so changing it later also needs an `ALTER USER`):

```sh
aws ssm put-parameter --name /sentinelstream/POSTGRES_HOST --type String --value postgres
aws ssm put-parameter --name /sentinelstream/POSTGRES_DB   --type String --value sentinelstream
aws ssm put-parameter --name /sentinelstream/POSTGRES_USER --type String --value sentinelstream
aws ssm put-parameter --name /sentinelstream/POSTGRES_PASSWORD --type SecureString --value "$(openssl rand -hex 32)"
```

The ingestion jobs need two more. The deploy does not require them, but `ingest_sec_filings` and `ingest_prices` runs fail with a clear error until they are set. Containers read Parameter Store only at start, so redeploy (or re-run the Deploy workflow) after adding them:

```sh
# SEC asks for an application name and a contact e-mail.
aws ssm put-parameter --name /sentinelstream/SEC_USER_AGENT  --type String       --value "SentinelStream <your contact e-mail>"
aws ssm put-parameter --name /sentinelstream/MASSIVE_API_KEY --type SecureString --value "<your Massive API key>"
```

High alerts go to Slack through an incoming webhook ([ADR 0009](../../docs/decisions/0009-slack-notifier.md)). Until it is set, a poll that has a high alert to send ends `partial` and the alert stays queued. The URL lets anyone post to the channel, so store it as a SecureString and redeploy:

```sh
aws ssm put-parameter --name /sentinelstream/SLACK_WEBHOOK_URL --type SecureString --value "https://hooks.slack.com/services/..."
```

## Reaching the host and UI

No inbound ports are open. Use Session Manager:

```sh
ID="$(terraform -chdir=infra/terraform/stage1 output -raw instance_id)"
aws ssm start-session --target "$ID"                       # shell
aws ssm start-session --target "$ID" \
  --document-name AWS-StartPortForwardingSession \
  --parameters '{"portNumber":["8000"],"localPortNumber":["8000"]}'
# then open http://127.0.0.1:8000
```
