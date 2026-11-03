"""Nexus fabric adapter — AWS VPC."""
from __future__ import annotations

import logging

try:
    import boto3
    from botocore.exceptions import ClientError, NoCredentialsError
except ImportError:
    boto3 = None

from .base import FabricAdapter, register_adapter

log = logging.getLogger("nexus.fabric.aws_vpc")


@register_adapter
class AWSVPCAdapter(FabricAdapter):
    TYPE_ID = "aws_vpc"
    DISPLAY_NAME = "AWS VPC"
    CATEGORY = "cloud"
    FIELDS = [
        {"name": "region", "type": "text", "label": "Region",
         "required": True, "hint": "e.g. us-east-1", "secret": False},
        {"name": "vpc_id", "type": "text", "label": "VPC ID",
         "required": True, "hint": "vpc-xxxxxxxx", "secret": False},
        {"name": "access_key_id", "type": "password", "label": "Access Key ID",
         "required": True, "hint": "AWS access key", "secret": True},
        {"name": "secret_access_key", "type": "password", "label": "Secret Access Key",
         "required": True, "hint": "AWS secret key", "secret": True},
        {"name": "session_token", "type": "password", "label": "Session Token",
         "required": False, "hint": "Temporary session token", "secret": True},
        {"name": "role_arn", "type": "text", "label": "Role ARN",
         "required": False, "hint": "IAM role to assume", "secret": False},
    ]

    def get_dispatch_modes(self) -> list[str]:
        return ["ssh", "broker"]

    def get_probe_interval(self) -> int:
        return 300

    def _client(self, config: dict):
        if boto3 is None:
            return None
        kwargs = {
            "region_name": config.get("region", "us-east-1"),
            "aws_access_key_id": config.get("access_key_id", ""),
            "aws_secret_access_key": config.get("secret_access_key", ""),
        }
        tok = config.get("session_token", "")
        if tok:
            kwargs["aws_session_token"] = tok
        sts_role = config.get("role_arn", "").strip()
        if sts_role:
            sts = boto3.client("sts", **kwargs)
            creds = sts.assume_role(RoleArn=sts_role, RoleSessionName="nexus")["Credentials"]
            kwargs["aws_access_key_id"] = creds["AccessKeyId"]
            kwargs["aws_secret_access_key"] = creds["SecretAccessKey"]
            kwargs["aws_session_token"] = creds["SessionToken"]
        return boto3.client("ec2", **kwargs)

    def test_connection(self, config: dict) -> dict:
        checks = []
        if boto3 is None:
            checks.append({"label": "boto3 installed", "passed": False, "detail": "pip install boto3"})
            return {"ok": False, "checks": checks}
        checks.append({"label": "boto3 installed", "passed": True, "detail": ""})

        try:
            ec2 = self._client(config)
            ec2.describe_vpcs(VpcIds=[config.get("vpc_id", "")])
            checks.append({"label": "VPC accessible", "passed": True, "detail": config.get("vpc_id", "")})
        except Exception as e:
            checks.append({"label": "VPC accessible", "passed": False, "detail": str(e)[:200]})

        return {"ok": all(c["passed"] for c in checks), "checks": checks}

    def discover_nodes(self, config: dict) -> list[dict]:
        if boto3 is None:
            return []
        try:
            ec2 = self._client(config)
            if not ec2:
                return []
            paginator = ec2.get_paginator("describe_instances")
            filters = [{"Name": "vpc-id", "Values": [config.get("vpc_id", "")]}]
            nodes = []
            for page in paginator.paginate(Filters=filters):
                for res in page.get("Reservations", []):
                    for inst in res.get("Instances", []):
                        priv_ip = inst.get("PrivateIpAddress", "")
                        if not priv_ip:
                            continue
                        tags_raw = {t["Key"]: t["Value"] for t in inst.get("Tags", [])}
                        hostname = tags_raw.get("Name", inst.get("InstanceId", priv_ip))
                        nodes.append({
                            "address": priv_ip,
                            "hostname": hostname,
                            "tags": list(tags_raw.keys()),
                            "metadata": {
                                "instance_id": inst.get("InstanceId", ""),
                                "public_ip": inst.get("PublicIpAddress", ""),
                                "instance_type": inst.get("InstanceType", ""),
                                "state": inst.get("State", {}).get("Name", ""),
                                "az": inst.get("Placement", {}).get("AvailabilityZone", ""),
                                "tags": tags_raw,
                            },
                        })
            return nodes
        except Exception:
            log.exception("AWS VPC discover failed")
            return []

    def check_health(self, config: dict) -> dict:
        if boto3 is None:
            return {"status": "down", "detail": {"error": "boto3 not installed"}}
        try:
            ec2 = self._client(config)
            vpcs = ec2.describe_vpcs(VpcIds=[config.get("vpc_id", "")])
            vpc_state = vpcs["Vpcs"][0]["State"] if vpcs.get("Vpcs") else "missing"
            nodes = self.discover_nodes(config)
            running = sum(1 for n in nodes if n["metadata"].get("state") == "running")
            status = "healthy" if vpc_state == "available" and running > 0 else "degraded"
            return {
                "status": status,
                "detail": {
                    "vpc_state": vpc_state,
                    "instances_total": len(nodes),
                    "instances_running": running,
                },
            }
        except Exception as e:
            return {"status": "down", "detail": {"error": str(e)[:200]}}

    def check_node_reachability(self, config: dict, address: str) -> bool:
        nodes = self.discover_nodes(config)
        for n in nodes:
            if n["address"] == address:
                return n["metadata"].get("state") == "running"
        return False
