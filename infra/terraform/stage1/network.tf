# One public subnet, no NAT gateway and no load balancer (they dominate cost at this size).
# The instance gets a public IPv4 address for outbound traffic only; the security group
# allows no inbound traffic. Operators connect with SSM Session Manager.

data "aws_ec2_instance_type_offerings" "available" {
  location_type = "availability-zone"
  filter {
    name   = "instance-type"
    values = [var.instance_type]
  }
}

# The zone is fixed rather than derived from the offerings: the data volume cannot move zones, so
# a zone that changed with instance_type would make Terraform try to replace it.
locals {
  availability_zone = var.availability_zone
}

resource "aws_vpc" "main" {
  cidr_block           = "10.40.0.0/16"
  enable_dns_support   = true
  enable_dns_hostnames = true
  tags                 = { Name = var.project }
}

resource "aws_internet_gateway" "main" {
  vpc_id = aws_vpc.main.id
  tags   = { Name = var.project }
}

resource "aws_subnet" "public" {
  vpc_id                  = aws_vpc.main.id
  cidr_block              = "10.40.1.0/24"
  availability_zone       = local.availability_zone
  map_public_ip_on_launch = true
  tags                    = { Name = "${var.project}-public" }

  lifecycle {
    precondition {
      condition     = contains(data.aws_ec2_instance_type_offerings.available.locations, var.availability_zone)
      error_message = "The chosen availability_zone does not offer the chosen instance_type."
    }
  }
}

resource "aws_route_table" "public" {
  vpc_id = aws_vpc.main.id
  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.main.id
  }
  tags = { Name = "${var.project}-public" }
}

resource "aws_route_table_association" "public" {
  subnet_id      = aws_subnet.public.id
  route_table_id = aws_route_table.public.id
}

resource "aws_security_group" "app" {
  name        = "${var.project}-app"
  description = "SentinelStream host: no inbound, outbound HTTP(S) only"
  vpc_id      = aws_vpc.main.id
  tags        = { Name = "${var.project}-app" }
}

resource "aws_vpc_security_group_egress_rule" "https" {
  security_group_id = aws_security_group.app.id
  description       = "HTTPS: ECR, SSM, CloudWatch, S3, SEC EDGAR, data providers, Slack"
  ip_protocol       = "tcp"
  from_port         = 443
  to_port           = 443
  cidr_ipv4         = "0.0.0.0/0"
}

resource "aws_vpc_security_group_egress_rule" "http" {
  security_group_id = aws_security_group.app.id
  description       = "HTTP: OS package repositories"
  ip_protocol       = "tcp"
  from_port         = 80
  to_port           = 80
  cidr_ipv4         = "0.0.0.0/0"
}
