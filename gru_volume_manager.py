#!/usr/bin/env python3

import boto3
from botocore.config import Config
import urllib.request
from pprint import pprint
import subprocess
import time
from pathlib import Path


def get_ec2_metadata(path):
    full_path = 'http://169.254.169.254/latest/meta-data{}'.format(path)
    return urllib.request.urlopen(full_path).read().decode()

def get_instance_id():
    return get_ec2_metadata('/instance-id')

def get_gru_data_version():
    return get_ec2_metadata('/tags/instance/gru-data-version')

def sync_dir(src, dst):
    subprocess.run(['/mnt/gru/rclone', 'sync', src, dst, '--progress'])


region = 'us-east-1'
availability_zone = 'us-east-1a'
data_dir_name = 'gru_data_' + get_gru_data_version()
volume_device = 'xvdx'

config = Config(
    region_name = region,
)

client = boto3.client('ec2', config=config)


res = client.describe_volumes(
    Filters=[
        {
            'Name': 'tag:Name',
            'Values': [
                data_dir_name,
            ],
        }
    ],
)

found = False
for volume in res['Volumes']:
    if volume['State'] == 'available':

        print("Found available volume: {}".format(volume['VolumeId']))

        found = True
        break

if not found:
    print("No volume available. Creating a new one")

    volume = client.create_volume(
        AvailabilityZone=availability_zone,
        Size=136,
        VolumeType='gp2',
        TagSpecifications=[
            {
                'ResourceType': 'volume',
                'Tags': [
                    {
                        'Key': 'Name',
                        'Value': data_dir_name,
                    }
                ],
            }
        ],
    )

    print("Created volume {}".format(volume['VolumeId']))

    waiter_config = {
        'Delay': 1,
    }
    client.get_waiter('volume_available').wait(VolumeIds=[volume['VolumeId']], WaiterConfig=waiter_config)

instance_id = get_instance_id()

print("Attaching volume")

attach_res = client.attach_volume(
        Device=volume_device,
        InstanceId=instance_id,
        VolumeId=volume['VolumeId'],
)

# TODO: figure out if we can avoid hard coding this. See https://stackoverflow.com/a/70212073/943814
#device = attach_res['Device']
device = 'nvme1n1'

waiter_config = {
    'Delay': 1,
}
client.get_waiter('volume_in_use').wait(VolumeIds=[volume['VolumeId']], WaiterConfig=waiter_config)

print("Waiting")
time.sleep(8)
print("Done waiting")

# It's a new volume, so we need to initialize it
if not found:
    subprocess.run(['mkfs.ext4', '/dev/{}'.format(device)])

Path('/mnt/ebs').mkdir(parents=True, exist_ok=True)

subprocess.run(['mount', '/dev/{}'.format(device), '/mnt/ebs'])


ebs_data_dir = '/mnt/ebs/{}'.format(data_dir_name)
efs_data_dir = '/mnt/gru/data/{}'.format(data_dir_name)

# It's a bit wasteful to run this sync command even for existing volumes, but
# it protects against volumes which may have been partially initialized for
# some reason.
sync_dir(efs_data_dir, ebs_data_dir)

subprocess.run(['chown', '-R', 'ubuntu:ubuntu', ebs_data_dir])
subprocess.run(['chmod', '-R', '-w', ebs_data_dir])

# populate the filesystem cache for the geneinfo db, because those requests tend to
# be slow at startup
subprocess.run(['cp', '{}/geneinfo/gene.iobio.db'.format(ebs_data_dir), '/dev/null'])


as_client = boto3.client('autoscaling', config=config)
as_client.complete_lifecycle_action(
    InstanceId=get_instance_id(),
    AutoScalingGroupName='gru-backend',
    LifecycleActionResult='CONTINUE',
    LifecycleHookName='gru-init',
)
