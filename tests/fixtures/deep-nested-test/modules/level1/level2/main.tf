variable "prefix" {}

resource "terraform_data" "l2" {
  input = "${var.prefix}-l2"
}

module "level3" {
  source = "./level3"
  prefix = terraform_data.l2.output
}

output "deep" {
  value = module.level3.value
}
