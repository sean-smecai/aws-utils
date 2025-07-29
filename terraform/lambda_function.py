#!/usr/bin/env python3
"""
AWS Auto-Shutdown Lambda Function
Stops EC2 instances, RDS instances, and other resources running longer than specified days
"""

import boto3
import json
import os
from datetime import datetime, timezone
from collections import defaultdict

# Configuration from environment variables
MAX_AGE_DAYS = int(os.environ.get('MAX_AGE_DAYS', '3'))
DRY_RUN = os.environ.get('DRY_RUN', 'false').lower() == 'true'
SNS_TOPIC_ARN = os.environ.get('SNS_TOPIC_ARN', '')
REGIONS = os.environ.get('REGIONS', 'us-east-1,us-west-2,ap-southeast-2').split(',')
LOG_LEVEL = os.environ.get('LOG_LEVEL', 'minimal')  # minimal|verbose

def log_verbose(message):
    """Print message only in verbose mode"""
    if LOG_LEVEL == 'verbose':
        print(message)

def log_minimal(message):
    """Print message in both minimal and verbose modes"""
    print(message)

def get_age_days(launch_time):
    """Calculate age in days from launch time"""
    if isinstance(launch_time, str):
        launch_time = datetime.fromisoformat(launch_time.replace('Z', '+00:00'))
    current_time = datetime.now(timezone.utc)
    age = current_time - launch_time
    return age.days

def shutdown_old_ec2_instances(ec2_client, region, summary):
    """Stop EC2 instances older than MAX_AGE_DAYS"""
    log_verbose(f"Checking EC2 instances in {region}...")
    
    response = ec2_client.describe_instances(
        Filters=[{'Name': 'instance-state-name', 'Values': ['running']}]
    )
    
    for reservation in response['Reservations']:
        for instance in reservation['Instances']:
            instance_id = instance['InstanceId']
            launch_time = instance['LaunchTime']
            age_days = get_age_days(launch_time)
            
            # Get instance name tag
            name = next((tag['Value'] for tag in instance.get('Tags', []) 
                        if tag['Key'] == 'Name'), 'Unnamed')
            
            if age_days >= MAX_AGE_DAYS:
                log_minimal(f"EC2 {instance_id} ({name}) in {region} - {age_days} days old")
                summary['ec2_instances'].append({
                    'id': instance_id,
                    'name': name,
                    'region': region,
                    'age_days': age_days,
                    'type': instance['InstanceType']
                })
                
                if not DRY_RUN:
                    try:
                        ec2_client.stop_instances(InstanceIds=[instance_id])
                        
                        # Tag the instance
                        ec2_client.create_tags(
                            Resources=[instance_id],
                            Tags=[
                                {'Key': 'AutoShutdown', 'Value': datetime.now(timezone.utc).strftime('%Y-%m-%d')},
                                {'Key': 'AutoShutdownReason', 'Value': f'Running-for-{age_days}-days'}
                            ]
                        )
                        log_minimal(f"Stopped EC2 {instance_id}")
                    except Exception as e:
                        log_minimal(f"Error stopping EC2 {instance_id}: {e}")
                        summary['errors'].append(f"EC2 {instance_id}: {str(e)}")

def shutdown_old_rds_instances(rds_client, region, summary):
    """Stop RDS instances older than MAX_AGE_DAYS"""
    log_verbose(f"Checking RDS instances in {region}...")
    
    try:
        response = rds_client.describe_db_instances()
        
        for db in response['DBInstances']:
            if db['DBInstanceStatus'] == 'available':
                db_id = db['DBInstanceIdentifier']
                create_time = db['InstanceCreateTime']
                age_days = get_age_days(create_time)
                
                if age_days >= MAX_AGE_DAYS:
                    log_minimal(f"RDS {db_id} in {region} - {age_days} days old")
                    summary['rds_instances'].append({
                        'id': db_id,
                        'region': region,
                        'age_days': age_days,
                        'type': db['DBInstanceClass']
                    })
                    
                    if not DRY_RUN:
                        try:
                            rds_client.stop_db_instance(DBInstanceIdentifier=db_id)
                            
                            # Tag the RDS instance
                            rds_client.add_tags_to_resource(
                                ResourceName=db['DBInstanceArn'],
                                Tags=[
                                    {'Key': 'AutoShutdown', 'Value': datetime.now(timezone.utc).strftime('%Y-%m-%d')},
                                    {'Key': 'AutoShutdownReason', 'Value': f'Running-for-{age_days}-days'}
                                ]
                            )
                            log_minimal(f"Stopped RDS {db_id}")
                        except Exception as e:
                            log_minimal(f"Error stopping RDS {db_id}: {e}")
                            summary['errors'].append(f"RDS {db_id}: {str(e)}")
    except Exception as e:
        log_verbose(f"Error listing RDS instances in {region}: {e}")

def shutdown_old_ecs_services(ecs_client, region, summary):
    """Scale down ECS services older than MAX_AGE_DAYS"""
    log_verbose(f"Checking ECS services in {region}...")
    
    try:
        clusters = ecs_client.list_clusters()['clusterArns']
        
        for cluster in clusters:
            services = ecs_client.list_services(cluster=cluster)['serviceArns']
            
            if services:
                service_details = ecs_client.describe_services(
                    cluster=cluster,
                    services=services
                )['services']
                
                for service in service_details:
                    if service['desiredCount'] > 0:
                        service_name = service['serviceName']
                        created_at = service['createdAt']
                        age_days = get_age_days(created_at)
                        
                        if age_days >= MAX_AGE_DAYS:
                            log_minimal(f"ECS {service_name} in {region} - {age_days} days old")
                            summary['ecs_services'].append({
                                'name': service_name,
                                'cluster': cluster.split('/')[-1],
                                'region': region,
                                'age_days': age_days,
                                'desired_count': service['desiredCount']
                            })
                            
                            if not DRY_RUN:
                                try:
                                    ecs_client.update_service(
                                        cluster=cluster,
                                        service=service['serviceArn'],
                                        desiredCount=0
                                    )
                                    log_minimal(f"Scaled down ECS {service_name}")
                                except Exception as e:
                                    log_minimal(f"Error scaling down ECS {service_name}: {e}")
                                    summary['errors'].append(f"ECS {service_name}: {str(e)}")
    except Exception as e:
        log_verbose(f"Error listing ECS services in {region}: {e}")

def cleanup_nat_gateways(ec2_client, region, summary):
    """Delete NAT Gateways older than MAX_AGE_DAYS"""
    log_verbose(f"Checking NAT Gateways in {region}...")
    
    try:
        response = ec2_client.describe_nat_gateways(
            Filter=[{'Name': 'state', 'Values': ['available']}]
        )
        
        for nat in response['NatGateways']:
            nat_id = nat['NatGatewayId']
            create_time = nat['CreateTime']
            age_days = get_age_days(create_time)
            
            if age_days >= MAX_AGE_DAYS:
                log_minimal(f"NAT Gateway {nat_id} in {region} - {age_days} days old")
                summary['nat_gateways'].append({
                    'id': nat_id,
                    'region': region,
                    'age_days': age_days
                })
                
                if not DRY_RUN:
                    try:
                        ec2_client.delete_nat_gateway(NatGatewayId=nat_id)
                        log_minimal(f"Deleted NAT Gateway {nat_id}")
                    except Exception as e:
                        log_minimal(f"Error deleting NAT Gateway {nat_id}: {e}")
                        summary['errors'].append(f"NAT {nat_id}: {str(e)}")
    except Exception as e:
        log_verbose(f"Error listing NAT Gateways in {region}: {e}")

def lambda_handler(event, context):
    """Main Lambda handler"""
    # Allow overriding config via event payload
    global MAX_AGE_DAYS, DRY_RUN
    
    if 'max_age_days' in event:
        MAX_AGE_DAYS = int(event['max_age_days'])
    
    if 'dry_run' in event:
        DRY_RUN = bool(event['dry_run'])
    
    log_minimal(f"AWS Auto-Shutdown - Age: {MAX_AGE_DAYS}d, DryRun: {DRY_RUN}, Regions: {len(REGIONS)}")
    
    # Summary for reporting
    summary = {
        'timestamp': datetime.now(timezone.utc).isoformat(),
        'max_age_days': MAX_AGE_DAYS,
        'dry_run': DRY_RUN,
        'ec2_instances': [],
        'rds_instances': [],
        'ecs_services': [],
        'nat_gateways': [],
        'errors': []
    }
    
    # Process each region
    for region in REGIONS:
        log_verbose(f"Processing region: {region}")
        
        try:
            # EC2
            ec2_client = boto3.client('ec2', region_name=region)
            shutdown_old_ec2_instances(ec2_client, region, summary)
            
            # RDS
            rds_client = boto3.client('rds', region_name=region)
            shutdown_old_rds_instances(rds_client, region, summary)
            
            # ECS
            ecs_client = boto3.client('ecs', region_name=region)
            shutdown_old_ecs_services(ecs_client, region, summary)
            
            # NAT Gateways
            cleanup_nat_gateways(ec2_client, region, summary)
            
        except Exception as e:
            log_minimal(f"Error processing region {region}: {e}")
            summary['errors'].append(f"Region {region}: {str(e)}")
    
    # Calculate totals
    total_resources = (
        len(summary['ec2_instances']) +
        len(summary['rds_instances']) +
        len(summary['ecs_services']) +
        len(summary['nat_gateways'])
    )
    
    # Final summary (always logged)
    log_minimal(f"Summary: {total_resources} resources found - EC2:{len(summary['ec2_instances'])}, RDS:{len(summary['rds_instances'])}, ECS:{len(summary['ecs_services'])}, NAT:{len(summary['nat_gateways'])}, Errors:{len(summary['errors'])}")
    
    # Send SNS notification if resources were shut down
    if total_resources > 0 and SNS_TOPIC_ARN:
        send_notification(summary, total_resources)
    
    return {
        'statusCode': 200,
        'body': json.dumps({
            'message': f"Auto-shutdown completed. {total_resources} resources processed.",
            'dry_run': DRY_RUN,
            'summary': summary
        })
    }

def send_notification(summary, total_resources):
    """Send SNS notification with shutdown summary"""
    sns_client = boto3.client('sns')
    
    subject = f"AWS Auto-Shutdown: {total_resources} resources {'identified' if DRY_RUN else 'stopped'}"
    
    message = f"""AWS Auto-Shutdown Report
{'=' * 50}
Mode: {'DRY RUN' if DRY_RUN else 'EXECUTED'}
Time: {summary['timestamp']}
Max Age: {MAX_AGE_DAYS} days

Resources Summary:
- EC2 Instances: {len(summary['ec2_instances'])}
- RDS Instances: {len(summary['rds_instances'])}
- ECS Services: {len(summary['ecs_services'])}
- NAT Gateways: {len(summary['nat_gateways'])}

"""
    
    if summary['ec2_instances']:
        message += "\nEC2 Instances:\n"
        for inst in summary['ec2_instances']:
            message += f"  - {inst['id']} ({inst['name']}) in {inst['region']} - {inst['age_days']} days old\n"
    
    if summary['rds_instances']:
        message += "\nRDS Instances:\n"
        for db in summary['rds_instances']:
            message += f"  - {db['id']} in {db['region']} - {db['age_days']} days old\n"
    
    if summary['errors']:
        message += f"\nErrors ({len(summary['errors'])}):\n"
        for error in summary['errors']:
            message += f"  - {error}\n"
    
    if not DRY_RUN:
        message += """
To restart resources:
- EC2: aws ec2 start-instances --instance-ids <instance-id>
- RDS: aws rds start-db-instance --db-instance-identifier <db-id>
- ECS: aws ecs update-service --cluster <cluster> --service <service> --desired-count <count>
"""
    
    try:
        sns_client.publish(
            TopicArn=SNS_TOPIC_ARN,
            Subject=subject,
            Message=message
        )
        log_minimal(f"Notification sent")
    except Exception as e:
        log_minimal(f"Error sending notification: {e}")

if __name__ == "__main__":
    # For local testing
    lambda_handler({}, None)