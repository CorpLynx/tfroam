variable "region" {
  type    = string
  default = "us-east-1"
}

variable "azs" {
  type    = list(string)
  default = ["us-east-1a", "us-east-1b"]
}

variable "web_ami" {
  description = "Made-up image ID; the demo is never applied."
  type        = string
  default     = "ami-0123456789abcdef0"
}

variable "certificate_arn" {
  description = "Made-up certificate ARN; the demo is never applied."
  type        = string
  default     = "arn:aws:acm:us-east-1:111122223333:certificate/00000000-0000-0000-0000-000000000000"
}
