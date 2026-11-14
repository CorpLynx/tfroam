output "transit_gateway_id" {
  value = aws_ec2_transit_gateway.core.id
}

output "web_url" {
  value = "https://${aws_route53_record.web.fqdn}"
}

output "orders_db_endpoint" {
  value = aws_db_instance.orders.address
}

output "vpc_ids" {
  value = {
    egress = module.vpc_egress.vpc_id
    prod   = module.vpc_prod.vpc_id
    dev    = module.vpc_dev.vpc_id
  }
}
