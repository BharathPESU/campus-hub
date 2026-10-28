output "bucket_name" {
  description = "The name of the S3 bucket storing Terraform remote state"
  value       = aws_s3_bucket.state.id
}
