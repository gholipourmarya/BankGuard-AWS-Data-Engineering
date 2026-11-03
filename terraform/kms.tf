# Customer-managed key for the data lake bucket.
# A customer-managed key has a small monthly charge. After `terraform destroy` it stays in
# "pending deletion" for the waiting period and can still be seen in the console.
resource "aws_kms_key" "lake" {
  description             = "${var.project_name} data lake encryption"
  enable_key_rotation     = true
  deletion_window_in_days = 7
}

resource "aws_kms_alias" "lake" {
  name          = "alias/${var.project_name}-lake"
  target_key_id = aws_kms_key.lake.key_id
}
