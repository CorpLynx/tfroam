variable "prefix" {
  default = "root"
}

module "level1" {
  source = "./modules/level1"
  prefix = var.prefix
}

module "remote" {
  source = "git::https://github.com/im2nguyen/rover.git//example/simple-test"
}

output "from_level3" {
  value = module.level1.deep
}
