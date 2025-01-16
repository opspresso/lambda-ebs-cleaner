import boto3
import datetime
import os
import requests
import time

from boto3.dynamodb.conditions import Key


DYNAMODB_TABLE_NAME = os.environ.get("DYNAMODB_TABLE_NAME", "lambda-ebs-cleaner")

REGIONS = {
    "ap-northeast-1": "jp",
    "ap-northeast-2": "kr",
    "ca-central-1": "ca",
    "eu-west-2": "gb",
    "us-west-2": "us",
}

RETENTION_DAYS = int(os.environ.get("RETENTION_DAYS", 0))

EXPIRE_SEC = 3600 * 24 * (RETENTION_DAYS + 2)

EXCLUDED_CLUSTERS = os.environ.get("EXCLUDED_CLUSTERS", "").split(",")
EXCLUDED_NAMESPACES = os.environ.get("EXCLUDED_NAMESPACES", "").split(",")

# Slack
SLACK_WEBHOOK_URL = os.environ["SLACK_WEBHOOK_URL"]

SLACK_CHANNEL = os.environ.get("SLACK_CHANNEL", "sandbox")

# DynamoDB
dynamodb = boto3.resource("dynamodb")
table = dynamodb.Table(DYNAMODB_TABLE_NAME)

# EC2
client_ec2 = boto3.client("ec2")


# Get the count from DynamoDB
def count_context(context):
    res = table.scan(FilterExpression=Key("context").eq(context))
    return len(res["Items"])


# Get the context from DynamoDB
def get_context(id, default=""):
    item = table.get_item(Key={"id": id}).get("Item")
    return (item["context"]) if item else (default)


# Put the context in DynamoDB
def put_context(id, context="", expire_sec=EXPIRE_SEC):
    expire_at = int(time.time()) + expire_sec
    expire_dt = datetime.datetime.fromtimestamp(expire_at).isoformat()
    table.put_item(
        Item={
            "id": id,
            "context": context,
            "expire_dt": expire_dt,
            "expire_at": expire_at,
        }
    )


# Delete the context from DynamoDB
def del_context(id):
    table.delete_item(Key={"id": id})


# Get the describe in EBS
def read_ebs():
    # include volumes created by ebs-csi-driver
    response = client_ec2.describe_volumes(
        Filters=[
            {"Name": "status", "Values": ["available"]},
            {"Name": "tag:ebs.csi.aws.com/cluster", "Values": ["true"]},
        ]
    )

    # exclude volumes created by kontrol-argo-workflows or in prod-kr-db kubernetes cluster
    def reverse_filter(volume):
        tags = volume.get("Tags", [])
        match_conditions = list(
            filter(
                lambda tag: (
                    (
                        tag["Key"] == "kubernetes.io/created-for/pvc/namespace"
                        and tag["Value"] in EXCLUDED_NAMESPACES
                    )
                    or (
                        tag["Key"] == "KubernetesCluster"
                        and tag["Value"] in EXCLUDED_CLUSTERS
                    )
                ),
                tags,
            )
        )
        if len(match_conditions) == 0:
            return True
        return False

    volumes = list(filter(reverse_filter, response["Volumes"]))
    results = []

    for volume in volumes:
        id = volume["VolumeId"]

        region = volume["AvailabilityZone"][:-1]
        country = REGIONS[region]

        name = None
        environment = None
        k8s_cluster = None

        for tag in volume["Tags"]:
            if tag["Key"] == "Name":
                name = tag["Value"]
            if tag["Key"] == "Country":
                country = tag["Value"]
            if tag["Key"] == "Environment":
                environment = tag["Value"]
            if tag["Key"] == "KubernetesCluster":
                k8s_cluster = tag["Value"]

        results.append(
            {
                "id": id,
                "name": name,
                "region": region,
                "country": country,
                "environment": environment,
                "k8s_cluster": k8s_cluster,
            }
        )

    return results


def gen_message(volume):
    message = {
        "channel": SLACK_CHANNEL,
        "icon_emoji": ":minidisc:",
        "username": "ebs-cleaner",
        "text": "사용하지 않는 EBS 볼륨을 삭제 합니다.",
        "attachments": [{"color": "warning", "fields": []}],
    }

    message["attachments"][0]["fields"].append(
        {
            "title": "Cluster" if volume["k8s_cluster"] != None else "Region",
            "value": ":flag-{}: {}".format(
                volume["country"],
                (
                    volume["k8s_cluster"]
                    if volume["k8s_cluster"] != None
                    else volume["region"]
                ),
            ),
        }
    )

    message["attachments"][0]["fields"].append(
        {
            "title": "Id",
            "value": volume["id"],
        }
    )

    message["attachments"][0]["fields"].append(
        {
            "title": "Name",
            "value": volume["name"],
        }
    )

    return message


# Send the message to Slack
def send_message(message):
    if SLACK_WEBHOOK_URL == "":
        return

    print("send_message: {}".format(message))
    response = requests.post(SLACK_WEBHOOK_URL, json=message)
    print("send_message: {}".format(response))


def cleaning():
    if RETENTION_DAYS == 0:
        return

    volumes = read_ebs()

    current_time = datetime.datetime.now()

    for volume in volumes:
        print("cleaning: {}".format(volume))

        volume_id = volume["id"]

        # Get the saved_time
        saved_time = get_context(volume_id)

        if saved_time == "":
            print("cleaning: {}".format(volume_id))

            # Save the volume_id and current_time
            put_context(volume_id, current_time.isoformat())
        else:
            previous_time = datetime.datetime.fromisoformat(saved_time)

            if (current_time - previous_time).days >= RETENTION_DAYS:
                print("cleaning: {} {}".format(volume_id, saved_time))

                # Delete the volume in EBS
                response = client_ec2.delete_volume(VolumeId=volume_id)
                print("cleaning: {}".format(response))

                # Delete the context from DynamoDB
                del_context(volume_id)

                # Send the message to Slack
                message = gen_message(volume)
                send_message(message)


def lambda_handler(event, context):
    print("lambda_handler: {}".format(event))

    cleaning()

    return {
        "statusCode": 200,
        "body": "ok",
    }
