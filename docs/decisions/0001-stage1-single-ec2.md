# 0001: Stage 1 runs on one EC2 instance with Docker Compose

Status: accepted, 21 Nov 2026

## Context

SentinelStream has one user, a handful of background jobs and a small Postgres database. It must run on AWS from the first milestone, deploy on every merge to `main`, and cost US$30 a month or less. A managed setup (RDS, ECS Fargate behind a load balancer, NAT gateway for private subnets) would cost several times that before any traffic.

## Decision

- One `t4g.small` (arm64) instance in us-east-1 runs Docker Compose with `api`, `scheduler`, `worker` and `postgres`.
- One application image serves `api`, `scheduler` and `worker`; Compose chooses the command. The built web UI is served by the API, so there is no separate frontend container or nginx.
- No load balancer and no NAT gateway. The instance has no inbound ports; the UI is reached through SSM Session Manager port forwarding.
- GitHub Actions authenticates with OIDC, pushes images to ECR tagged by commit SHA, and deploys through SSM Run Command. Secrets live in SSM Parameter Store.
- Terraform is applied from the owner's machine. The CI role can push images and run the deploy command, not change infrastructure.

## Consequences

- Cheap and simple, and every piece is still infrastructure as code.
- Single point of failure: an instance loss means restoring Postgres from the nightly S3 backup. Acceptable for a personal tool.
- Images must be built for linux/arm64.
- Stage 2 (RDS, ECS Fargate, S3 + CloudFront) remains an optional move after M4; keeping one image and env-based config keeps that move small.
