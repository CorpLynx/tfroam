# The prod web tier: a load balancer in front of two web servers, an app server and a database.

resource "aws_security_group" "lb" {
  name   = "prod-lb"
  vpc_id = module.vpc_prod.vpc_id
}

resource "aws_security_group" "web" {
  name   = "prod-web"
  vpc_id = module.vpc_prod.vpc_id
}

resource "aws_security_group" "db" {
  name   = "prod-db"
  vpc_id = module.vpc_prod.vpc_id
}

resource "aws_vpc_security_group_ingress_rule" "lb_https" {
  security_group_id = aws_security_group.lb.id
  cidr_ipv4         = "10.0.0.0/8"
  from_port         = 443
  to_port           = 443
  ip_protocol       = "tcp"
}

resource "aws_vpc_security_group_ingress_rule" "web_from_lb" {
  security_group_id            = aws_security_group.web.id
  referenced_security_group_id = aws_security_group.lb.id
  from_port                    = 8080
  to_port                      = 8080
  ip_protocol                  = "tcp"
}

resource "aws_vpc_security_group_ingress_rule" "db_from_web" {
  security_group_id            = aws_security_group.db.id
  referenced_security_group_id = aws_security_group.web.id
  from_port                    = 5432
  to_port                      = 5432
  ip_protocol                  = "tcp"
}

resource "aws_instance" "web" {
  count                  = 2
  ami                    = var.web_ami
  instance_type          = "t3.small"
  subnet_id              = module.vpc_prod.private_subnet_ids[count.index]
  vpc_security_group_ids = [aws_security_group.web.id]
  tags                   = { Name = "web-${count.index + 1}" }
}

resource "aws_lb" "web" {
  name               = "prod-web"
  internal           = true
  load_balancer_type = "application"
  subnets            = module.vpc_prod.private_subnet_ids
  security_groups    = [aws_security_group.lb.id]
}

resource "aws_lb_target_group" "web" {
  name     = "prod-web"
  port     = 8080
  protocol = "HTTP"
  vpc_id   = module.vpc_prod.vpc_id
}

resource "aws_lb_target_group_attachment" "web" {
  count            = 2
  target_group_arn = aws_lb_target_group.web.arn
  target_id        = aws_instance.web[count.index].id
}

resource "aws_lb_listener" "https" {
  load_balancer_arn = aws_lb.web.arn
  port              = 443
  protocol          = "HTTPS"
  certificate_arn   = var.certificate_arn
  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.web.arn
  }
}

resource "aws_db_subnet_group" "prod" {
  name       = "prod"
  subnet_ids = module.vpc_prod.private_subnet_ids
}

resource "aws_db_instance" "orders" {
  identifier                  = "orders"
  engine                      = "postgres"
  instance_class              = "db.t3.medium"
  allocated_storage           = 50
  username                    = "orders"
  manage_master_user_password = true
  db_subnet_group_name        = aws_db_subnet_group.prod.name
  vpc_security_group_ids      = [aws_security_group.db.id]
  skip_final_snapshot         = true
}

resource "aws_route53_zone" "internal" {
  name = "acme.internal"
  vpc {
    vpc_id = module.vpc_prod.vpc_id
  }
}

resource "aws_route53_record" "web" {
  zone_id = aws_route53_zone.internal.zone_id
  name    = "web.acme.internal"
  type    = "A"
  alias {
    name                   = aws_lb.web.dns_name
    zone_id                = aws_lb.web.zone_id
    evaluate_target_health = true
  }
}

resource "aws_route53_record" "orders_db" {
  zone_id = aws_route53_zone.internal.zone_id
  name    = "orders-db.acme.internal"
  type    = "CNAME"
  ttl     = 300
  records = [aws_db_instance.orders.address]
}
