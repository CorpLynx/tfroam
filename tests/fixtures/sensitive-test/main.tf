variable "db_password" {
  type      = string
  default   = "var-default-SECRET"
  sensitive = true
}

variable "region" {
  type    = string
  default = "us-east-1"
}

resource "aws_db_instance" "db" {
  username = "admin"
  password = var.db_password
  tags = {
    env   = "prod"
    token = var.db_password
  }
}

resource "random_password" "pw" {
  length = 16
}

output "conn" {
  value = {
    host     = "db.example.com"
    password = aws_db_instance.db.password
  }
  sensitive = true
}

output "region" {
  value = var.region
}
