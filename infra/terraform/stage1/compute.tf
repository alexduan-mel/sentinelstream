data "aws_ssm_parameter" "al2023_arm64" {
  name = "/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-arm64"
}

locals {
  data_mount_point = "/srv/${var.project}"
}

# Postgres data lives here, not on the root volume, so replacing the instance keeps the database.
resource "aws_ebs_volume" "data" {
  availability_zone = local.availability_zone
  size              = var.data_volume_gb
  type              = "gp3"
  encrypted         = true
  tags              = { Name = "${var.project}-data", Snapshot = "daily" }

  lifecycle {
    prevent_destroy = true
  }
}

resource "aws_instance" "app" {
  ami                    = data.aws_ssm_parameter.al2023_arm64.value
  instance_type          = var.instance_type
  subnet_id              = aws_subnet.public.id
  vpc_security_group_ids = [aws_security_group.app.id]
  iam_instance_profile   = aws_iam_instance_profile.app.name

  metadata_options {
    http_tokens                 = "required" # IMDSv2 only
    http_endpoint               = "enabled"
    http_put_response_hop_limit = 2 # lets containers on the Docker bridge reach instance credentials
  }

  root_block_device {
    volume_type           = "gp3"
    volume_size           = var.root_volume_gb
    encrypted             = true
    delete_on_termination = true
  }

  user_data = templatefile("${path.module}/user_data.sh.tftpl", {
    data_volume_id = aws_ebs_volume.data.id
    mount_point    = local.data_mount_point
    project        = var.project
  })
  user_data_replace_on_change = true

  tags = { Name = var.project }

  lifecycle {
    # A new AL2023 release should not silently replace the instance; replace deliberately.
    ignore_changes = [ami]
  }
}

resource "aws_volume_attachment" "data" {
  device_name = "/dev/sdf"
  volume_id   = aws_ebs_volume.data.id
  instance_id = aws_instance.app.id
}

# Daily snapshots of the data volume via Data Lifecycle Manager. Snapshots are incremental,
# so a week of them costs well under US$1 a month. Covers the database until the nightly
# pg_dump to S3 exists (M1), and keeps protecting it after.
resource "aws_iam_role" "dlm" {
  name = "${var.project}-dlm"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "dlm.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy_attachment" "dlm" {
  role       = aws_iam_role.dlm.name
  policy_arn = "arn:${local.partition}:iam::aws:policy/service-role/AWSDataLifecycleManagerServiceRole"
}

resource "aws_dlm_lifecycle_policy" "data_daily" {
  description        = "${var.project} data volume daily snapshots"
  execution_role_arn = aws_iam_role.dlm.arn
  state              = "ENABLED"

  policy_details {
    resource_types = ["VOLUME"]
    target_tags    = { Snapshot = "daily" }

    schedule {
      name      = "daily-${var.snapshot_retention_days}d"
      copy_tags = true

      create_rule {
        interval      = 24
        interval_unit = "HOURS"
        times         = ["07:00"] # UTC, overnight in US markets
      }

      retain_rule {
        count = var.snapshot_retention_days
      }

      tags_to_add = { SnapshotCreator = "dlm" }
    }
  }

  depends_on = [aws_iam_role_policy_attachment.dlm]
}
