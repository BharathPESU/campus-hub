locals {
  routes = {
    "GET /api/health" = {
      function = "health"
      auth     = "NONE"
    }
    "GET /api/events" = {
      function = "list_events"
      auth     = "NONE"
    }
    "GET /api/events/{id}" = {
      function = "get_event"
      auth     = "NONE"
    }
    "POST /api/events" = {
      function = "create_event"
      auth     = "JWT"
    }
    "POST /api/events/{id}/cancel" = {
      function = "start_cancel"
      auth     = "JWT"
    }
    "POST /api/events/{id}/registrations" = {
      function = "register"
      auth     = "JWT"
    }
    "DELETE /api/events/{id}/registrations/me" = {
      function = "unregister"
      auth     = "JWT"
    }
    "GET /api/me/registrations" = {
      function = "list_my_registrations"
      auth     = "JWT"
    }
  }
}

resource "aws_apigatewayv2_api" "http" {
  name          = "${var.prefix}-api"
  protocol_type = "HTTP"
}

resource "aws_cloudwatch_log_group" "api" {
  name              = "/aws/apigateway/${var.prefix}-api"
  retention_in_days = 14
}

resource "aws_apigatewayv2_stage" "default" {
  api_id      = aws_apigatewayv2_api.http.id
  name        = "$default"
  auto_deploy = true

  access_log_settings {
    destination_arn = aws_cloudwatch_log_group.api.arn
    format = jsonencode({
      requestId       = "$context.requestId"
      ip              = "$context.identity.sourceIp"
      requestTime     = "$context.requestTime"
      httpMethod      = "$context.httpMethod"
      routeKey        = "$context.routeKey"
      status          = "$context.status"
      protocol        = "$context.protocol"
      responseLength  = "$context.responseLength"
      errorMessage    = "$context.error.message"
      authorizerError = "$context.authorizer.error"
    })
  }

  default_route_settings {
    throttling_burst_limit = 40
    throttling_rate_limit  = 20
  }
}

resource "aws_apigatewayv2_authorizer" "jwt" {
  api_id           = aws_apigatewayv2_api.http.id
  authorizer_type  = "JWT"
  identity_sources = ["$request.header.Authorization"]
  name             = "${var.prefix}-cognito-jwt"

  jwt_configuration {
    audience = [var.client_id]
    issuer   = var.issuer
  }
}

resource "aws_apigatewayv2_integration" "routes" {
  for_each = local.routes

  api_id                 = aws_apigatewayv2_api.http.id
  integration_type       = "AWS_PROXY"
  integration_method     = "POST"
  payload_format_version = "2.0"
  integration_uri        = var.function_invoke_arns[each.value.function]
}

resource "aws_apigatewayv2_route" "routes" {
  for_each = local.routes

  api_id    = aws_apigatewayv2_api.http.id
  route_key = each.key
  target    = "integrations/${aws_apigatewayv2_integration.routes[each.key].id}"

  authorization_type = each.value.auth
  authorizer_id      = each.value.auth == "JWT" ? aws_apigatewayv2_authorizer.jwt.id : null
}

resource "aws_lambda_permission" "api" {
  for_each = local.routes

  statement_id  = "AllowAPIGW-${replace(replace(replace(replace(each.key, " ", "-"), "/", "-"), "{", ""), "}", "")}"
  action        = "lambda:InvokeFunction"
  function_name = var.function_arns[each.value.function]
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.http.execution_arn}/*/*"
}
