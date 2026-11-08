variable "prefix" {}

resource "terraform_data" "l3" {
  count = 2
  input = "${var.prefix}-l3-${count.index}"
}

output "value" {
  value = terraform_data.l3[0].output
}
