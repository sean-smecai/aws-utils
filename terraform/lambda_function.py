#!/usr/bin/env python3
"""
AWS Auto-Shutdown Lambda Function
Stops EC2 instances, RDS instances, and other resources running longer than specified days
"""

import boto3
import json
import os
import time
import traceback
import uuid
from datetime import datetime, timezone, timedelta
from collections import defaultdict
from typing import Dict, List, Any, Optional

# Configuration from environment variables
MAX_AGE_DAYS = int(os.environ.get('MAX_AGE_DAYS', '3'))
DRY_RUN = os.environ.get('DRY_RUN', 'false').lower() == 'true'
SNS_TOPIC_ARN = os.environ.get('SNS_TOPIC_ARN', '')
REGIONS = os.environ.get('REGIONS', 'us-east-1,us-west-2,ap-southeast-2').split(',')
LOG_LEVEL = os.environ.get('LOG_LEVEL', 'minimal')  # minimal|verbose

# Exclusion patterns for resource protection
S3_BUCKET_EXCLUSIONS = os.environ.get('S3_BUCKET_EXCLUSIONS', 'terraform-state,cloudtrail,logs,backup').split(',')
ELB_NAME_EXCLUSIONS = os.environ.get('ELB_NAME_EXCLUSIONS', 'production,critical').split(',')
ES_DOMAIN_EXCLUSIONS = os.environ.get('ES_DOMAIN_EXCLUSIONS', 'production,logs').split(',')

# CloudWatch metrics client
cloudwatch = boto3.client('cloudwatch', region_name='us-east-1')

# Global correlation ID for this execution
CORRELATION_ID = str(uuid.uuid4())

# Performance tracking
PERFORMANCE_METRICS = {
    'start_time': None,
    'region_times': {},
    'resource_counts': defaultdict(int),
    'api_call_latencies': []
}

def structured_log(level: str, message: str, **kwargs) -> None:
    """Create structured JSON log entry"""
    log_entry = {
        'timestamp': datetime.now(timezone.utc).isoformat(),
        'level': level,
        'correlation_id': CORRELATION_ID,
        'message': message,
        'dry_run': DRY_RUN,
        **kwargs
    }
    print(json.dumps(log_entry))

def log_verbose(message):
    """Print message only in verbose mode"""
    if LOG_LEVEL == 'verbose':
        structured_log('DEBUG', message)

def log_minimal(message):
    """Print message in both minimal and verbose modes"""
    structured_log('INFO', message)

def log_error(message: str, error: Exception = None, **kwargs) -> None:
    """Log error with stack trace"""
    error_details = {
        'error_type': type(error).__name__ if error else 'Unknown',
        'error_message': str(error) if error else message,
        'stack_trace': traceback.format_exc() if error else None
    }
    structured_log('ERROR', message, **error_details, **kwargs)

def log_performance(operation: str, duration: float, **kwargs) -> None:
    """Log performance metrics"""
    if LOG_LEVEL == 'verbose':
        structured_log('METRIC', f"Performance: {operation}", 
                      operation=operation, 
                      duration_ms=round(duration * 1000, 2),
                      **kwargs)
    PERFORMANCE_METRICS['api_call_latencies'].append({
        'operation': operation,
        'duration': duration,
        **kwargs
    })

def publish_cloudwatch_metric(metric_name: str, value: float, unit: str = 'Count', 
                             dimensions: List[Dict] = None) -> None:
    """Publish custom metric to CloudWatch"""
    try:
        metric_data = {
            'MetricName': metric_name,
            'Value': value,
            'Unit': unit,
            'Timestamp': datetime.now(timezone.utc),
            'Dimensions': dimensions or [
                {'Name': 'Environment', 'Value': 'Production'},
                {'Name': 'DryRun', 'Value': str(DRY_RUN)}
            ]
        }
        
        cloudwatch.put_metric_data(
            Namespace='AWS/AutoShutdown',
            MetricData=[metric_data]
        )
        
        if LOG_LEVEL == 'verbose':
            structured_log('DEBUG', f"Published metric: {metric_name}={value}")
    except Exception as e:
        log_error(f"Failed to publish metric {metric_name}", e)

def get_age_days(launch_time):
    """Calculate age in days from launch time"""
    if isinstance(launch_time, str):
        launch_time = datetime.fromisoformat(launch_time.replace('Z', '+00:00'))
    current_time = datetime.now(timezone.utc)
    age = current_time - launch_time
    return age.days

def shutdown_old_ec2_instances(ec2_client, region, summary):
    """Stop EC2 instances older than MAX_AGE_DAYS"""
    operation_start = time.time()
    
    structured_log('INFO', f"Scanning EC2 instances", 
                  region=region, 
                  operation='ec2_scan',
                  max_age_days=MAX_AGE_DAYS)
    
    try:
        api_start = time.time()
        response = ec2_client.describe_instances(
            Filters=[{'Name': 'instance-state-name', 'Values': ['running']}]
        )
        log_performance('ec2_describe_instances', time.time() - api_start, region=region)
        
        instances_processed = 0
        instances_shutdown = 0
        
        for reservation in response['Reservations']:
            for instance in reservation['Instances']:
                instances_processed += 1
                instance_id = instance['InstanceId']
                launch_time = instance['LaunchTime']
                age_days = get_age_days(launch_time)
                
                # Get instance name tag
                name = next((tag['Value'] for tag in instance.get('Tags', []) 
                            if tag['Key'] == 'Name'), 'Unnamed')
                
                # Log resource state before action
                structured_log('DEBUG' if LOG_LEVEL == 'verbose' else 'INFO',
                             "EC2 instance found",
                             resource_type='ec2',
                             resource_id=instance_id,
                             resource_name=name,
                             region=region,
                             age_days=age_days,
                             instance_type=instance['InstanceType'],
                             state='running',
                             action_required=age_days >= MAX_AGE_DAYS)
                
                if age_days >= MAX_AGE_DAYS:
                    summary['ec2_instances'].append({
                        'id': instance_id,
                        'name': name,
                        'region': region,
                        'age_days': age_days,
                        'type': instance['InstanceType']
                    })
                    
                    if not DRY_RUN:
                        try:
                            shutdown_start = time.time()
                            ec2_client.stop_instances(InstanceIds=[instance_id])
                            
                            # Tag the instance
                            ec2_client.create_tags(
                                Resources=[instance_id],
                                Tags=[
                                    {'Key': 'AutoShutdown', 'Value': datetime.now(timezone.utc).strftime('%Y-%m-%d')},
                                    {'Key': 'AutoShutdownReason', 'Value': f'Running-for-{age_days}-days'}
                                ]
                            )
                            
                            shutdown_duration = time.time() - shutdown_start
                            log_performance('ec2_stop_instance', shutdown_duration, 
                                          instance_id=instance_id, region=region)
                            
                            instances_shutdown += 1
                            
                            structured_log('INFO', "EC2 instance stopped successfully",
                                         resource_type='ec2',
                                         resource_id=instance_id,
                                         resource_name=name,
                                         region=region,
                                         age_days=age_days,
                                         action='stopped',
                                         duration_ms=round(shutdown_duration * 1000, 2))
                            
                            # Publish success metric
                            publish_cloudwatch_metric('EC2InstancesStopped', 1,
                                                     dimensions=[
                                                         {'Name': 'Region', 'Value': region},
                                                         {'Name': 'InstanceType', 'Value': instance['InstanceType']}
                                                     ])
                        except Exception as e:
                            log_error(f"Failed to stop EC2 instance", e,
                                    resource_type='ec2',
                                    resource_id=instance_id,
                                    region=region)
                            summary['errors'].append(f"EC2 {instance_id}: {str(e)}")
                            
                            # Publish failure metric
                            publish_cloudwatch_metric('EC2InstancesFailedToStop', 1,
                                                     dimensions=[
                                                         {'Name': 'Region', 'Value': region},
                                                         {'Name': 'ErrorType', 'Value': type(e).__name__}
                                                     ])
                    else:
                        instances_shutdown += 1  # Count for dry-run
        
        # Log operation summary
        operation_duration = time.time() - operation_start
        structured_log('INFO', "EC2 scan completed",
                      region=region,
                      instances_processed=instances_processed,
                      instances_shutdown=instances_shutdown,
                      duration_ms=round(operation_duration * 1000, 2))
        
        # Update performance metrics
        PERFORMANCE_METRICS['resource_counts']['ec2'] += instances_shutdown
        
    except Exception as e:
        log_error(f"Failed to scan EC2 instances in {region}", e, region=region)
        summary['errors'].append(f"EC2 scan {region}: {str(e)}")

def shutdown_old_rds_instances(rds_client, region, summary):
    """Stop RDS instances older than MAX_AGE_DAYS"""
    operation_start = time.time()
    
    structured_log('INFO', "Scanning RDS instances",
                  region=region,
                  operation='rds_scan',
                  max_age_days=MAX_AGE_DAYS)
    
    try:
        api_start = time.time()
        response = rds_client.describe_db_instances()
        log_performance('rds_describe_instances', time.time() - api_start, region=region)
        
        instances_processed = 0
        instances_shutdown = 0
        
        for db in response['DBInstances']:
            if db['DBInstanceStatus'] == 'available':
                instances_processed += 1
                db_id = db['DBInstanceIdentifier']
                create_time = db['InstanceCreateTime']
                age_days = get_age_days(create_time)
                
                # Log resource state before action
                structured_log('DEBUG' if LOG_LEVEL == 'verbose' else 'INFO',
                             "RDS instance found",
                             resource_type='rds',
                             resource_id=db_id,
                             region=region,
                             age_days=age_days,
                             instance_class=db['DBInstanceClass'],
                             engine=db.get('Engine', 'unknown'),
                             state='available',
                             action_required=age_days >= MAX_AGE_DAYS)
                
                if age_days >= MAX_AGE_DAYS:
                    summary['rds_instances'].append({
                        'id': db_id,
                        'region': region,
                        'age_days': age_days,
                        'type': db['DBInstanceClass']
                    })
                    
                    if not DRY_RUN:
                        try:
                            shutdown_start = time.time()
                            rds_client.stop_db_instance(DBInstanceIdentifier=db_id)
                            
                            # Tag the RDS instance
                            rds_client.add_tags_to_resource(
                                ResourceName=db['DBInstanceArn'],
                                Tags=[
                                    {'Key': 'AutoShutdown', 'Value': datetime.now(timezone.utc).strftime('%Y-%m-%d')},
                                    {'Key': 'AutoShutdownReason', 'Value': f'Running-for-{age_days}-days'}
                                ]
                            )
                            
                            shutdown_duration = time.time() - shutdown_start
                            log_performance('rds_stop_instance', shutdown_duration,
                                          db_id=db_id, region=region)
                            
                            instances_shutdown += 1
                            
                            structured_log('INFO', "RDS instance stopped successfully",
                                         resource_type='rds',
                                         resource_id=db_id,
                                         region=region,
                                         age_days=age_days,
                                         action='stopped',
                                         duration_ms=round(shutdown_duration * 1000, 2))
                            
                            # Publish success metric
                            publish_cloudwatch_metric('RDSInstancesStopped', 1,
                                                     dimensions=[
                                                         {'Name': 'Region', 'Value': region},
                                                         {'Name': 'Engine', 'Value': db.get('Engine', 'unknown')}
                                                     ])
                        except Exception as e:
                            log_error(f"Failed to stop RDS instance", e,
                                    resource_type='rds',
                                    resource_id=db_id,
                                    region=region)
                            summary['errors'].append(f"RDS {db_id}: {str(e)}")
                            
                            # Publish failure metric
                            publish_cloudwatch_metric('RDSInstancesFailedToStop', 1,
                                                     dimensions=[
                                                         {'Name': 'Region', 'Value': region},
                                                         {'Name': 'ErrorType', 'Value': type(e).__name__}
                                                     ])
                    else:
                        instances_shutdown += 1  # Count for dry-run
        
        # Log operation summary
        operation_duration = time.time() - operation_start
        structured_log('INFO', "RDS scan completed",
                      region=region,
                      instances_processed=instances_processed,
                      instances_shutdown=instances_shutdown,
                      duration_ms=round(operation_duration * 1000, 2))
        
        # Update performance metrics
        PERFORMANCE_METRICS['resource_counts']['rds'] += instances_shutdown
        
    except Exception as e:
        log_error(f"Failed to scan RDS instances in {region}", e, region=region)
        summary['errors'].append(f"RDS scan {region}: {str(e)}")

def shutdown_old_ecs_services(ecs_client, region, summary):
    """Scale down ECS services older than MAX_AGE_DAYS"""
    operation_start = time.time()
    
    structured_log('INFO', "Scanning ECS services",
                  region=region,
                  operation='ecs_scan',
                  max_age_days=MAX_AGE_DAYS)
    
    try:
        api_start = time.time()
        clusters = ecs_client.list_clusters()['clusterArns']
        log_performance('ecs_list_clusters', time.time() - api_start, region=region)
        
        services_processed = 0
        services_scaled = 0
        
        for cluster in clusters:
            cluster_name = cluster.split('/')[-1]
            
            try:
                api_start = time.time()
                services = ecs_client.list_services(cluster=cluster)['serviceArns']
                log_performance('ecs_list_services', time.time() - api_start, 
                              region=region, cluster=cluster_name)
                
                if services:
                    api_start = time.time()
                    service_details = ecs_client.describe_services(
                        cluster=cluster,
                        services=services
                    )['services']
                    log_performance('ecs_describe_services', time.time() - api_start,
                                  region=region, cluster=cluster_name)
                    
                    for service in service_details:
                        if service['desiredCount'] > 0:
                            services_processed += 1
                            service_name = service['serviceName']
                            created_at = service['createdAt']
                            age_days = get_age_days(created_at)
                            
                            # Log resource state before action
                            structured_log('DEBUG' if LOG_LEVEL == 'verbose' else 'INFO',
                                         "ECS service found",
                                         resource_type='ecs',
                                         resource_id=service_name,
                                         cluster=cluster_name,
                                         region=region,
                                         age_days=age_days,
                                         desired_count=service['desiredCount'],
                                         running_count=service.get('runningCount', 0),
                                         state='active',
                                         action_required=age_days >= MAX_AGE_DAYS)
                            
                            if age_days >= MAX_AGE_DAYS:
                                summary['ecs_services'].append({
                                    'name': service_name,
                                    'cluster': cluster_name,
                                    'region': region,
                                    'age_days': age_days,
                                    'desired_count': service['desiredCount']
                                })
                                
                                if not DRY_RUN:
                                    try:
                                        scale_start = time.time()
                                        ecs_client.update_service(
                                            cluster=cluster,
                                            service=service['serviceArn'],
                                            desiredCount=0
                                        )
                                        
                                        scale_duration = time.time() - scale_start
                                        log_performance('ecs_scale_service', scale_duration,
                                                      service=service_name, cluster=cluster_name, region=region)
                                        
                                        services_scaled += 1
                                        
                                        structured_log('INFO', "ECS service scaled down successfully",
                                                     resource_type='ecs',
                                                     resource_id=service_name,
                                                     cluster=cluster_name,
                                                     region=region,
                                                     age_days=age_days,
                                                     action='scaled_to_zero',
                                                     previous_count=service['desiredCount'],
                                                     duration_ms=round(scale_duration * 1000, 2))
                                        
                                        # Publish success metric
                                        publish_cloudwatch_metric('ECSServicesScaledDown', 1,
                                                                 dimensions=[
                                                                     {'Name': 'Region', 'Value': region},
                                                                     {'Name': 'Cluster', 'Value': cluster_name}
                                                                 ])
                                    except Exception as e:
                                        log_error(f"Failed to scale down ECS service", e,
                                                resource_type='ecs',
                                                resource_id=service_name,
                                                cluster=cluster_name,
                                                region=region)
                                        summary['errors'].append(f"ECS {service_name}: {str(e)}")
                                        
                                        # Publish failure metric
                                        publish_cloudwatch_metric('ECSServicesFailedToScale', 1,
                                                                 dimensions=[
                                                                     {'Name': 'Region', 'Value': region},
                                                                     {'Name': 'ErrorType', 'Value': type(e).__name__}
                                                                 ])
                                else:
                                    services_scaled += 1  # Count for dry-run
                                    
            except Exception as e:
                log_error(f"Failed to process ECS cluster {cluster_name}", e,
                        region=region, cluster=cluster_name)
        
        # Log operation summary
        operation_duration = time.time() - operation_start
        structured_log('INFO', "ECS scan completed",
                      region=region,
                      services_processed=services_processed,
                      services_scaled=services_scaled,
                      duration_ms=round(operation_duration * 1000, 2))
        
        # Update performance metrics
        PERFORMANCE_METRICS['resource_counts']['ecs'] += services_scaled
        
    except Exception as e:
        log_error(f"Failed to scan ECS services in {region}", e, region=region)
        summary['errors'].append(f"ECS scan {region}: {str(e)}")

def cleanup_nat_gateways(ec2_client, region, summary):
    """Delete NAT Gateways older than MAX_AGE_DAYS"""
    operation_start = time.time()
    
    structured_log('INFO', "Scanning NAT Gateways",
                  region=region,
                  operation='nat_scan',
                  max_age_days=MAX_AGE_DAYS)
    
    try:
        api_start = time.time()
        response = ec2_client.describe_nat_gateways(
            Filter=[{'Name': 'state', 'Values': ['available']}]
        )
        log_performance('nat_describe_gateways', time.time() - api_start, region=region)
        
        gateways_processed = 0
        gateways_deleted = 0
        
        for nat in response['NatGateways']:
            gateways_processed += 1
            nat_id = nat['NatGatewayId']
            create_time = nat['CreateTime']
            age_days = get_age_days(create_time)
            
            # Log resource state before action
            structured_log('DEBUG' if LOG_LEVEL == 'verbose' else 'INFO',
                         "NAT Gateway found",
                         resource_type='nat_gateway',
                         resource_id=nat_id,
                         region=region,
                         age_days=age_days,
                         vpc_id=nat.get('VpcId', 'unknown'),
                         subnet_id=nat.get('SubnetId', 'unknown'),
                         state='available',
                         action_required=age_days >= MAX_AGE_DAYS)
            
            if age_days >= MAX_AGE_DAYS:
                summary['nat_gateways'].append({
                    'id': nat_id,
                    'region': region,
                    'age_days': age_days
                })
                
                if not DRY_RUN:
                    try:
                        delete_start = time.time()
                        ec2_client.delete_nat_gateway(NatGatewayId=nat_id)
                        
                        delete_duration = time.time() - delete_start
                        log_performance('nat_delete_gateway', delete_duration,
                                      nat_id=nat_id, region=region)
                        
                        gateways_deleted += 1
                        
                        structured_log('INFO', "NAT Gateway deleted successfully",
                                     resource_type='nat_gateway',
                                     resource_id=nat_id,
                                     region=region,
                                     age_days=age_days,
                                     action='deleted',
                                     duration_ms=round(delete_duration * 1000, 2))
                        
                        # Publish success metric
                        publish_cloudwatch_metric('NATGatewaysDeleted', 1,
                                                 dimensions=[
                                                     {'Name': 'Region', 'Value': region},
                                                     {'Name': 'VpcId', 'Value': nat.get('VpcId', 'unknown')}
                                                 ])
                    except Exception as e:
                        log_error(f"Failed to delete NAT Gateway", e,
                                resource_type='nat_gateway',
                                resource_id=nat_id,
                                region=region)
                        summary['errors'].append(f"NAT {nat_id}: {str(e)}")
                        
                        # Publish failure metric
                        publish_cloudwatch_metric('NATGatewaysFailedToDelete', 1,
                                                 dimensions=[
                                                     {'Name': 'Region', 'Value': region},
                                                     {'Name': 'ErrorType', 'Value': type(e).__name__}
                                                 ])
                else:
                    gateways_deleted += 1  # Count for dry-run
        
        # Log operation summary
        operation_duration = time.time() - operation_start
        structured_log('INFO', "NAT Gateway scan completed",
                      region=region,
                      gateways_processed=gateways_processed,
                      gateways_deleted=gateways_deleted,
                      duration_ms=round(operation_duration * 1000, 2))
        
        # Update performance metrics
        PERFORMANCE_METRICS['resource_counts']['nat'] += gateways_deleted
        
    except Exception as e:
        log_error(f"Failed to scan NAT Gateways in {region}", e, region=region)
        summary['errors'].append(f"NAT scan {region}: {str(e)}")

def is_resource_excluded(resource_name: str, exclusion_patterns: List[str]) -> bool:
    """Check if resource name matches any exclusion pattern"""
    if not resource_name:
        return False
    
    resource_lower = resource_name.lower()
    for pattern in exclusion_patterns:
        if pattern and pattern.lower() in resource_lower:
            return True
    return False

def cleanup_old_load_balancers(region, summary):
    """Delete ELB/ALB load balancers older than MAX_AGE_DAYS"""
    operation_start = time.time()
    
    structured_log('INFO', "Scanning Load Balancers",
                  region=region,
                  operation='elb_scan',
                  max_age_days=MAX_AGE_DAYS)
    
    # Handle ELBv2 (ALB/NLB)
    try:
        elbv2_client = boto3.client('elbv2', region_name=region)
        api_start = time.time()
        response = elbv2_client.describe_load_balancers()
        log_performance('elbv2_describe_load_balancers', time.time() - api_start, region=region)
        
        lb_processed = 0
        lb_deleted = 0
        
        for lb in response['LoadBalancers']:
            lb_processed += 1
            lb_name = lb['LoadBalancerName']
            lb_arn = lb['LoadBalancerArn']
            created_time = lb['CreatedTime']
            age_days = get_age_days(created_time)
            
            # Check exclusions
            if is_resource_excluded(lb_name, ELB_NAME_EXCLUSIONS):
                structured_log('DEBUG', f"Load balancer excluded from cleanup",
                             resource_type='alb',
                             resource_id=lb_name,
                             region=region,
                             reason='exclusion_pattern_match')
                continue
            
            # Log resource state
            structured_log('DEBUG' if LOG_LEVEL == 'verbose' else 'INFO',
                         "Load balancer found",
                         resource_type='alb',
                         resource_id=lb_name,
                         region=region,
                         age_days=age_days,
                         type=lb['Type'],
                         scheme=lb['Scheme'],
                         state=lb['State']['Code'],
                         action_required=age_days >= MAX_AGE_DAYS)
            
            if age_days >= MAX_AGE_DAYS:
                summary.setdefault('load_balancers', []).append({
                    'name': lb_name,
                    'arn': lb_arn,
                    'type': lb['Type'],
                    'region': region,
                    'age_days': age_days
                })
                
                if not DRY_RUN:
                    try:
                        delete_start = time.time()
                        elbv2_client.delete_load_balancer(LoadBalancerArn=lb_arn)
                        
                        delete_duration = time.time() - delete_start
                        log_performance('elbv2_delete_load_balancer', delete_duration,
                                      lb_name=lb_name, region=region)
                        
                        lb_deleted += 1
                        
                        structured_log('INFO', "Load balancer deleted successfully",
                                     resource_type='alb',
                                     resource_id=lb_name,
                                     region=region,
                                     age_days=age_days,
                                     action='deleted',
                                     duration_ms=round(delete_duration * 1000, 2))
                        
                        # Publish success metric
                        publish_cloudwatch_metric('LoadBalancersDeleted', 1,
                                                 dimensions=[
                                                     {'Name': 'Region', 'Value': region},
                                                     {'Name': 'Type', 'Value': lb['Type']}
                                                 ])
                    except Exception as e:
                        log_error(f"Failed to delete load balancer", e,
                                resource_type='alb',
                                resource_id=lb_name,
                                region=region)
                        summary.setdefault('errors', []).append(f"ALB {lb_name}: {str(e)}")
                        
                        # Publish failure metric
                        publish_cloudwatch_metric('LoadBalancersFailedToDelete', 1,
                                                 dimensions=[
                                                     {'Name': 'Region', 'Value': region},
                                                     {'Name': 'ErrorType', 'Value': type(e).__name__}
                                                 ])
                else:
                    lb_deleted += 1  # Count for dry-run
        
        # Log operation summary
        operation_duration = time.time() - operation_start
        structured_log('INFO', "Load balancer scan completed",
                      region=region,
                      lb_processed=lb_processed,
                      lb_deleted=lb_deleted,
                      duration_ms=round(operation_duration * 1000, 2))
        
        # Update performance metrics
        PERFORMANCE_METRICS['resource_counts']['elb'] = lb_deleted
        
    except Exception as e:
        log_error(f"Failed to scan load balancers in {region}", e, region=region)
        summary.setdefault('errors', []).append(f"ELB scan {region}: {str(e)}")
    
    # Handle Classic ELB
    try:
        elb_client = boto3.client('elb', region_name=region)
        api_start = time.time()
        response = elb_client.describe_load_balancers()
        log_performance('elb_describe_load_balancers', time.time() - api_start, region=region)
        
        for lb in response['LoadBalancerDescriptions']:
            lb_processed += 1
            lb_name = lb['LoadBalancerName']
            created_time = lb['CreatedTime']
            age_days = get_age_days(created_time)
            
            # Check exclusions
            if is_resource_excluded(lb_name, ELB_NAME_EXCLUSIONS):
                continue
            
            if age_days >= MAX_AGE_DAYS:
                summary.setdefault('load_balancers', []).append({
                    'name': lb_name,
                    'type': 'classic',
                    'region': region,
                    'age_days': age_days
                })
                
                if not DRY_RUN:
                    try:
                        elb_client.delete_load_balancer(LoadBalancerName=lb_name)
                        lb_deleted += 1
                        structured_log('INFO', "Classic ELB deleted",
                                     resource_type='elb_classic',
                                     resource_id=lb_name,
                                     region=region,
                                     age_days=age_days)
                    except Exception as e:
                        log_error(f"Failed to delete classic ELB", e,
                                resource_type='elb_classic',
                                resource_id=lb_name,
                                region=region)
                else:
                    lb_deleted += 1
                    
    except Exception as e:
        log_error(f"Failed to scan classic ELBs in {region}", e, region=region)

def cleanup_old_s3_buckets(summary):
    """Delete S3 buckets older than MAX_AGE_DAYS (S3 is global but accessed via regions)"""
    operation_start = time.time()
    
    structured_log('INFO', "Scanning S3 buckets",
                  operation='s3_scan',
                  max_age_days=MAX_AGE_DAYS)
    
    try:
        s3_client = boto3.client('s3')
        api_start = time.time()
        response = s3_client.list_buckets()
        log_performance('s3_list_buckets', time.time() - api_start)
        
        buckets_processed = 0
        buckets_deleted = 0
        
        for bucket in response['Buckets']:
            buckets_processed += 1
            bucket_name = bucket['Name']
            created_time = bucket['CreationDate']
            age_days = get_age_days(created_time)
            
            # Check exclusions
            if is_resource_excluded(bucket_name, S3_BUCKET_EXCLUSIONS):
                structured_log('DEBUG', f"S3 bucket excluded from cleanup",
                             resource_type='s3',
                             resource_id=bucket_name,
                             reason='exclusion_pattern_match')
                continue
            
            # Log resource state
            structured_log('DEBUG' if LOG_LEVEL == 'verbose' else 'INFO',
                         "S3 bucket found",
                         resource_type='s3',
                         resource_id=bucket_name,
                         age_days=age_days,
                         action_required=age_days >= MAX_AGE_DAYS)
            
            if age_days >= MAX_AGE_DAYS:
                # Get bucket region
                try:
                    bucket_location = s3_client.get_bucket_location(Bucket=bucket_name)
                    bucket_region = bucket_location['LocationConstraint'] or 'us-east-1'
                except:
                    bucket_region = 'unknown'
                
                summary.setdefault('s3_buckets', []).append({
                    'name': bucket_name,
                    'region': bucket_region,
                    'age_days': age_days
                })
                
                if not DRY_RUN:
                    try:
                        # First, try to empty the bucket (only if it's small)
                        delete_start = time.time()
                        try:
                            # List objects (limit to check if empty or small)
                            objects = s3_client.list_objects_v2(Bucket=bucket_name, MaxKeys=100)
                            
                            if 'Contents' in objects and len(objects['Contents']) > 0:
                                if len(objects['Contents']) < 100:  # Only auto-delete if small bucket
                                    # Delete objects
                                    delete_objects = {'Objects': [{'Key': obj['Key']} for obj in objects['Contents']]}
                                    s3_client.delete_objects(Bucket=bucket_name, Delete=delete_objects)
                                    structured_log('INFO', f"Emptied small S3 bucket",
                                                 resource_id=bucket_name,
                                                 object_count=len(objects['Contents']))
                                else:
                                    # Skip large buckets
                                    structured_log('WARNING', f"S3 bucket not empty, skipping",
                                                 resource_id=bucket_name,
                                                 reason='bucket_not_empty')
                                    summary.setdefault('errors', []).append(f"S3 {bucket_name}: Bucket not empty (>100 objects)")
                                    continue
                        except:
                            pass  # Bucket might be empty
                        
                        # Delete the bucket
                        s3_client.delete_bucket(Bucket=bucket_name)
                        
                        delete_duration = time.time() - delete_start
                        log_performance('s3_delete_bucket', delete_duration, bucket_name=bucket_name)
                        
                        buckets_deleted += 1
                        
                        structured_log('INFO', "S3 bucket deleted successfully",
                                     resource_type='s3',
                                     resource_id=bucket_name,
                                     region=bucket_region,
                                     age_days=age_days,
                                     action='deleted',
                                     duration_ms=round(delete_duration * 1000, 2))
                        
                        # Publish success metric
                        publish_cloudwatch_metric('S3BucketsDeleted', 1)
                        
                    except Exception as e:
                        log_error(f"Failed to delete S3 bucket", e,
                                resource_type='s3',
                                resource_id=bucket_name)
                        summary.setdefault('errors', []).append(f"S3 {bucket_name}: {str(e)}")
                        
                        # Publish failure metric
                        publish_cloudwatch_metric('S3BucketsFailedToDelete', 1,
                                                 dimensions=[
                                                     {'Name': 'ErrorType', 'Value': type(e).__name__}
                                                 ])
                else:
                    buckets_deleted += 1  # Count for dry-run
        
        # Log operation summary
        operation_duration = time.time() - operation_start
        structured_log('INFO', "S3 scan completed",
                      buckets_processed=buckets_processed,
                      buckets_deleted=buckets_deleted,
                      duration_ms=round(operation_duration * 1000, 2))
        
        # Update performance metrics
        PERFORMANCE_METRICS['resource_counts']['s3'] = buckets_deleted
        
    except Exception as e:
        log_error(f"Failed to scan S3 buckets", e)
        summary.setdefault('errors', []).append(f"S3 scan: {str(e)}")

def cleanup_old_elasticsearch_domains(region, summary):
    """Delete Elasticsearch/OpenSearch domains older than MAX_AGE_DAYS"""
    operation_start = time.time()
    
    structured_log('INFO', "Scanning Elasticsearch/OpenSearch domains",
                  region=region,
                  operation='es_scan',
                  max_age_days=MAX_AGE_DAYS)
    
    try:
        # Try OpenSearch first (newer service)
        try:
            es_client = boto3.client('opensearch', region_name=region)
            service_type = 'opensearch'
        except:
            # Fall back to Elasticsearch
            es_client = boto3.client('es', region_name=region)
            service_type = 'elasticsearch'
        
        api_start = time.time()
        response = es_client.list_domain_names() if service_type == 'opensearch' else es_client.list_domain_names()
        log_performance(f'{service_type}_list_domains', time.time() - api_start, region=region)
        
        domains_processed = 0
        domains_deleted = 0
        
        for domain_info in response.get('DomainNames', []):
            domains_processed += 1
            domain_name = domain_info['DomainName']
            
            # Check exclusions
            if is_resource_excluded(domain_name, ES_DOMAIN_EXCLUSIONS):
                structured_log('DEBUG', f"ES domain excluded from cleanup",
                             resource_type=service_type,
                             resource_id=domain_name,
                             region=region,
                             reason='exclusion_pattern_match')
                continue
            
            # Get domain details
            try:
                if service_type == 'opensearch':
                    domain_config = es_client.describe_domain(DomainName=domain_name)
                    domain = domain_config['DomainStatus']
                else:
                    domain_config = es_client.describe_elasticsearch_domain(DomainName=domain_name)
                    domain = domain_config['DomainStatus']
                
                # Get creation time (if available)
                created_time = domain.get('Created')
                if not created_time:
                    # Use current time minus 30 days as fallback (conservative)
                    created_time = datetime.now(timezone.utc) - timedelta(days=30)
                
                age_days = get_age_days(created_time)
                
                # Log resource state
                structured_log('DEBUG' if LOG_LEVEL == 'verbose' else 'INFO',
                             f"{service_type.title()} domain found",
                             resource_type=service_type,
                             resource_id=domain_name,
                             region=region,
                             age_days=age_days,
                             endpoint=domain.get('Endpoint', 'N/A'),
                             processing=domain.get('Processing', False),
                             action_required=age_days >= MAX_AGE_DAYS)
                
                if age_days >= MAX_AGE_DAYS and not domain.get('Processing', False):
                    summary.setdefault('elasticsearch_domains', []).append({
                        'name': domain_name,
                        'type': service_type,
                        'region': region,
                        'age_days': age_days
                    })
                    
                    if not DRY_RUN:
                        try:
                            delete_start = time.time()
                            if service_type == 'opensearch':
                                es_client.delete_domain(DomainName=domain_name)
                            else:
                                es_client.delete_elasticsearch_domain(DomainName=domain_name)
                            
                            delete_duration = time.time() - delete_start
                            log_performance(f'{service_type}_delete_domain', delete_duration,
                                          domain_name=domain_name, region=region)
                            
                            domains_deleted += 1
                            
                            structured_log('INFO', f"{service_type.title()} domain deleted successfully",
                                         resource_type=service_type,
                                         resource_id=domain_name,
                                         region=region,
                                         age_days=age_days,
                                         action='deleted',
                                         duration_ms=round(delete_duration * 1000, 2))
                            
                            # Publish success metric
                            publish_cloudwatch_metric('ElasticsearchDomainsDeleted', 1,
                                                     dimensions=[
                                                         {'Name': 'Region', 'Value': region},
                                                         {'Name': 'ServiceType', 'Value': service_type}
                                                     ])
                        except Exception as e:
                            log_error(f"Failed to delete {service_type} domain", e,
                                    resource_type=service_type,
                                    resource_id=domain_name,
                                    region=region)
                            summary.setdefault('errors', []).append(f"ES {domain_name}: {str(e)}")
                            
                            # Publish failure metric
                            publish_cloudwatch_metric('ElasticsearchDomainsFailedToDelete', 1,
                                                     dimensions=[
                                                         {'Name': 'Region', 'Value': region},
                                                         {'Name': 'ErrorType', 'Value': type(e).__name__}
                                                     ])
                    else:
                        domains_deleted += 1  # Count for dry-run
                        
            except Exception as e:
                log_error(f"Failed to describe {service_type} domain {domain_name}", e,
                        domain_name=domain_name, region=region)
        
        # Log operation summary
        operation_duration = time.time() - operation_start
        structured_log('INFO', f"{service_type.title()} scan completed",
                      region=region,
                      domains_processed=domains_processed,
                      domains_deleted=domains_deleted,
                      duration_ms=round(operation_duration * 1000, 2))
        
        # Update performance metrics
        PERFORMANCE_METRICS['resource_counts']['elasticsearch'] = domains_deleted
        
    except Exception as e:
        log_error(f"Failed to scan Elasticsearch domains in {region}", e, region=region)
        summary.setdefault('errors', []).append(f"ES scan {region}: {str(e)}")

def lambda_handler(event, context):
    """Main Lambda handler"""
    # Initialize performance tracking
    PERFORMANCE_METRICS['start_time'] = time.time()
    execution_start = PERFORMANCE_METRICS['start_time']
    
    # Allow overriding config via event payload
    global MAX_AGE_DAYS, DRY_RUN, CORRELATION_ID
    
    # Generate new correlation ID for this execution
    CORRELATION_ID = str(uuid.uuid4())
    
    if 'max_age_days' in event:
        MAX_AGE_DAYS = int(event['max_age_days'])
    
    if 'dry_run' in event:
        DRY_RUN = bool(event['dry_run'])
    
    # Log execution start with context info
    structured_log('INFO', "AWS Auto-Shutdown execution started",
                  correlation_id=CORRELATION_ID,
                  max_age_days=MAX_AGE_DAYS,
                  dry_run=DRY_RUN,
                  regions=REGIONS,
                  region_count=len(REGIONS),
                  function_name=context.function_name if context else 'local',
                  request_id=context.aws_request_id if context else 'local',
                  memory_limit=context.memory_limit_in_mb if context else 'N/A')
    
    # Summary for reporting
    summary = {
        'timestamp': datetime.now(timezone.utc).isoformat(),
        'correlation_id': CORRELATION_ID,
        'max_age_days': MAX_AGE_DAYS,
        'dry_run': DRY_RUN,
        'ec2_instances': [],
        'rds_instances': [],
        'ecs_services': [],
        'nat_gateways': [],
        'load_balancers': [],  # New resource type
        's3_buckets': [],      # New resource type
        'elasticsearch_domains': [],  # New resource type
        'errors': [],
        'performance_metrics': {}
    }
    
    # Process each region
    for region in REGIONS:
        region_start = time.time()
        
        structured_log('INFO', f"Processing region",
                      region=region,
                      region_index=REGIONS.index(region) + 1,
                      total_regions=len(REGIONS))
        
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
            
            # Load Balancers (ELB/ALB/NLB)
            cleanup_old_load_balancers(region, summary)
            
            # Elasticsearch/OpenSearch Domains
            cleanup_old_elasticsearch_domains(region, summary)
            
            # Record region processing time
            region_duration = time.time() - region_start
            PERFORMANCE_METRICS['region_times'][region] = region_duration
            
            structured_log('INFO', "Region processing completed",
                          region=region,
                          duration_ms=round(region_duration * 1000, 2))
            
        except Exception as e:
            log_error(f"Failed to process region {region}", e, region=region)
            summary['errors'].append(f"Region {region}: {str(e)}")
            
            # Publish region failure metric
            publish_cloudwatch_metric('RegionProcessingFailed', 1,
                                     dimensions=[{'Name': 'Region', 'Value': region}])
    
    # S3 buckets are global, process them once after all regions
    try:
        cleanup_old_s3_buckets(summary)
    except Exception as e:
        log_error("Failed to process S3 buckets", e)
        summary['errors'].append(f"S3 buckets: {str(e)}")
    
    # Calculate totals
    total_resources = (
        len(summary['ec2_instances']) +
        len(summary['rds_instances']) +
        len(summary['ecs_services']) +
        len(summary['nat_gateways']) +
        len(summary.get('load_balancers', [])) +
        len(summary.get('s3_buckets', [])) +
        len(summary.get('elasticsearch_domains', []))
    )
    
    # Calculate execution metrics
    execution_duration = time.time() - execution_start
    
    # Add performance metrics to summary
    summary['performance_metrics'] = {
        'execution_duration_ms': round(execution_duration * 1000, 2),
        'regions_processed': len(REGIONS),
        'region_times': {k: round(v * 1000, 2) for k, v in PERFORMANCE_METRICS['region_times'].items()},
        'resource_counts': dict(PERFORMANCE_METRICS['resource_counts']),
        'average_api_latency_ms': round(
            sum(m['duration'] for m in PERFORMANCE_METRICS['api_call_latencies']) * 1000 / 
            max(len(PERFORMANCE_METRICS['api_call_latencies']), 1), 2
        ) if PERFORMANCE_METRICS['api_call_latencies'] else 0
    }
    
    # Log final summary
    structured_log('INFO', "AWS Auto-Shutdown execution completed",
                  correlation_id=CORRELATION_ID,
                  total_resources=total_resources,
                  ec2_count=len(summary['ec2_instances']),
                  rds_count=len(summary['rds_instances']),
                  ecs_count=len(summary['ecs_services']),
                  nat_count=len(summary['nat_gateways']),
                  error_count=len(summary['errors']),
                  execution_duration_ms=round(execution_duration * 1000, 2),
                  memory_used_mb=context.memory_limit_in_mb if context else 'N/A',
                  dry_run=DRY_RUN)
    
    # Publish summary metrics to CloudWatch
    publish_cloudwatch_metric('TotalResourcesProcessed', total_resources)
    publish_cloudwatch_metric('ExecutionDuration', execution_duration * 1000, unit='Milliseconds')
    publish_cloudwatch_metric('ExecutionErrors', len(summary['errors']))
    
    # Publish individual resource type metrics
    for resource_type, count in [
        ('EC2', len(summary['ec2_instances'])),
        ('RDS', len(summary['rds_instances'])),
        ('ECS', len(summary['ecs_services'])),
        ('NAT', len(summary['nat_gateways']))
    ]:
        if count > 0:
            publish_cloudwatch_metric(f'{resource_type}ResourcesProcessed', count)
    
    # Send SNS notification if resources were shut down
    if total_resources > 0 and SNS_TOPIC_ARN:
        send_notification(summary, total_resources)
    
    return {
        'statusCode': 200,
        'body': json.dumps({
            'message': f"Auto-shutdown completed. {total_resources} resources processed.",
            'correlation_id': CORRELATION_ID,
            'dry_run': DRY_RUN,
            'summary': summary
        })
    }

def send_notification(summary, total_resources):
    """Send SNS notification with enhanced shutdown summary"""
    sns_client = boto3.client('sns')
    
    subject = f"AWS Auto-Shutdown: {total_resources} resources {'identified' if DRY_RUN else 'stopped'}"
    
    message = f"""AWS Auto-Shutdown Report
{'=' * 50}
Mode: {'DRY RUN' if DRY_RUN else 'EXECUTED'}
Time: {summary['timestamp']}
Correlation ID: {summary.get('correlation_id', 'N/A')}
Max Age: {MAX_AGE_DAYS} days

Resources Summary:
- EC2 Instances: {len(summary['ec2_instances'])}
- RDS Instances: {len(summary['rds_instances'])}
- ECS Services: {len(summary['ecs_services'])}
- NAT Gateways: {len(summary['nat_gateways'])}
- Load Balancers: {len(summary.get('load_balancers', []))}
- S3 Buckets: {len(summary.get('s3_buckets', []))}
- Elasticsearch Domains: {len(summary.get('elasticsearch_domains', []))}

Performance Metrics:
- Execution Duration: {summary.get('performance_metrics', {}).get('execution_duration_ms', 'N/A')} ms
- Regions Processed: {summary.get('performance_metrics', {}).get('regions_processed', len(REGIONS))}
- Average API Latency: {summary.get('performance_metrics', {}).get('average_api_latency_ms', 'N/A')} ms

"""
    
    if summary['ec2_instances']:
        message += "\nEC2 Instances:\n"
        for inst in summary['ec2_instances'][:10]:  # Limit to first 10 to avoid message size limits
            message += f"  - {inst['id']} ({inst['name']}) in {inst['region']} - {inst['age_days']} days old\n"
        if len(summary['ec2_instances']) > 10:
            message += f"  ... and {len(summary['ec2_instances']) - 10} more\n"
    
    if summary['rds_instances']:
        message += "\nRDS Instances:\n"
        for db in summary['rds_instances'][:10]:  # Limit to first 10
            message += f"  - {db['id']} in {db['region']} - {db['age_days']} days old\n"
        if len(summary['rds_instances']) > 10:
            message += f"  ... and {len(summary['rds_instances']) - 10} more\n"
    
    if summary['ecs_services']:
        message += "\nECS Services:\n"
        for svc in summary['ecs_services'][:10]:  # Limit to first 10
            message += f"  - {svc['name']} in {svc['cluster']} ({svc['region']}) - {svc['age_days']} days old\n"
        if len(summary['ecs_services']) > 10:
            message += f"  ... and {len(summary['ecs_services']) - 10} more\n"
    
    if summary['nat_gateways']:
        message += "\nNAT Gateways:\n"
        for nat in summary['nat_gateways'][:10]:  # Limit to first 10
            message += f"  - {nat['id']} in {nat['region']} - {nat['age_days']} days old\n"
        if len(summary['nat_gateways']) > 10:
            message += f"  ... and {len(summary['nat_gateways']) - 10} more\n"
    
    if summary['errors']:
        message += f"\nErrors ({len(summary['errors'])}):\n"
        for error in summary['errors'][:5]:  # Limit to first 5 errors
            message += f"  - {error}\n"
        if len(summary['errors']) > 5:
            message += f"  ... and {len(summary['errors']) - 5} more errors\n"
    
    if not DRY_RUN:
        message += """
To restart resources:
- EC2: aws ec2 start-instances --instance-ids <instance-id>
- RDS: aws rds start-db-instance --db-instance-identifier <db-id>
- ECS: aws ecs update-service --cluster <cluster> --service <service> --desired-count <count>
"""
    
    message += f"""
CloudWatch Logs:
View detailed logs in CloudWatch Logs with correlation ID: {summary.get('correlation_id', 'N/A')}
"""
    
    try:
        sns_client.publish(
            TopicArn=SNS_TOPIC_ARN,
            Subject=subject,
            Message=message
        )
        structured_log('INFO', "SNS notification sent successfully",
                      topic_arn=SNS_TOPIC_ARN,
                      message_size=len(message))
    except Exception as e:
        log_error(f"Failed to send SNS notification", e, topic_arn=SNS_TOPIC_ARN)

if __name__ == "__main__":
    # For local testing
    lambda_handler({}, None)