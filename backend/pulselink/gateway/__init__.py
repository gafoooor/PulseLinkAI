"""API gateway / edge package for PulseLink.

Hosts the role-based access (RBAC) seam (``pulselink.gateway.rbac``) that, in
production, maps to **Amazon API Gateway + Lambda authorizers + IAM**. For the
offline demo it is a local stub: the authenticated caller (``Principal``) is
built from request headers instead of an API Gateway authorizer context.
"""
