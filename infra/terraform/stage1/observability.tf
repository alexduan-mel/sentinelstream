# Container logs, one alarm on failed task runs, and the notification topic it shares
# with the budget.

resource "aws_cloudwatch_log_group" "app" {
  name              = "/${var.project}/app"
  retention_in_days = var.log_retention_days
}

resource "aws_sns_topic" "alerts" {
  name = "${var.project}-ops-alerts"
}

# AWS emails a confirmation link; the subscription is pending until it is clicked.
resource "aws_sns_topic_subscription" "alerts_email" {
  topic_arn = aws_sns_topic.alerts.arn
  protocol  = "email"
  endpoint  = var.alert_email
}

# Lets AWS Budgets publish to the topic.
resource "aws_sns_topic_policy" "alerts" {
  arn = aws_sns_topic.alerts.arn
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid       = "AllowBudgets"
        Effect    = "Allow"
        Principal = { Service = "budgets.amazonaws.com" }
        Action    = "SNS:Publish"
        Resource  = aws_sns_topic.alerts.arn
        Condition = { StringEquals = { "aws:SourceAccount" = local.account_id } }
      },
      {
        Sid       = "AllowCloudWatchAlarms"
        Effect    = "Allow"
        Principal = { Service = "cloudwatch.amazonaws.com" }
        Action    = "SNS:Publish"
        Resource  = aws_sns_topic.alerts.arn
        Condition = { StringEquals = { "aws:SourceAccount" = local.account_id } }
      },
    ]
  })
}

# The orchestration layer logs one `task_run_failed` line each time a run ends as failed.
resource "aws_cloudwatch_log_metric_filter" "task_run_failed" {
  name           = "${var.project}-task-run-failed"
  log_group_name = aws_cloudwatch_log_group.app.name
  pattern        = "\"task_run_failed\""

  metric_transformation {
    name          = "TaskRunFailed"
    namespace     = "SentinelStream"
    value         = "1"
    default_value = "0"
  }
}

resource "aws_cloudwatch_metric_alarm" "task_run_failed" {
  alarm_name          = "${var.project}-task-run-failed"
  alarm_description   = "At least one scheduled task run ended as failed in the last 5 minutes."
  namespace           = "SentinelStream"
  metric_name         = aws_cloudwatch_log_metric_filter.task_run_failed.metric_transformation[0].name
  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 1
  comparison_operator = "GreaterThanOrEqualToThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.alerts.arn]
  ok_actions          = [aws_sns_topic.alerts.arn]
}
