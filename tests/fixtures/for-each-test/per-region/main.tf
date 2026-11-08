variable "region" {
  type = string
}

resource "terraform_data" "bucket" {
  input = var.region
}

output "bucket_input" {
  value = terraform_data.bucket.input
}
