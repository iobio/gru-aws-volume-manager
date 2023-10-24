import boto3
from botocore.config import Config
import urllib.request
from pprint import pprint
import subprocess
import time
from pathlib import Path
region = 'us-east-1'
availability_zone = 'us-east-1a'
version = '1.10.0'
data_dir_name = 'gru_data_' + version
def get_instance_id():
    return urllib.request.urlopen('http://169.254.169.254/latest/meta-data/instance-id').read().decode()
def sync_dir(src, dst):
    subprocess.run(['./rclone', 'sync', src, dst, '--progress'])
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
        print("Found available volume")
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
client.attach_volume(
        Device='/dev/sdx',
        InstanceId=instance_id,
        VolumeId=volume['VolumeId'],
)
# It's a new volume, so we need to initialize it
if not found:
    waiter_config = {
        'Delay': 1,
    }
    client.get_waiter('volume_in_use').wait(VolumeIds=[volume['VolumeId']], WaiterConfig=waiter_config)
    time.sleep(4)
    subprocess.run(['mkfs.ext4', '/dev/xvdx'])
Path('/mnt/ebs').mkdir(parents=True, exist_ok=True)
subprocess.run(['mount', '/dev/xvdx', '/mnt/ebs'])
if not found:
    efs_data_dir = '/mnt/gru/data/{}'.format(data_dir_name)
    ebs_data_dir = '/mnt/ebs/{}'.format(data_dir_name)
    sync_dir(efs_data_dir, ebs_data_dir)
