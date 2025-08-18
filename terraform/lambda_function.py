#!/usr/bin/env python3
"""
AWS Auto-Shutdown Lambda Function
Stops EC2 instances, RDS instances, and other resources running longer than specified days
"""

import boto3
import json
import os
import re
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

# Cost optimization configuration
COST_ANALYSIS_ENABLED = os.environ.get('COST_ANALYSIS_ENABLED', 'true').lower() == 'true'
HIGH_VALUE_THRESHOLD = float(os.environ.get('HIGH_VALUE_THRESHOLD', '100'))  # USD per month
COST_PRIORITY_THRESHOLD = float(os.environ.get('COST_PRIORITY_THRESHOLD', '50'))  # USD per month
SCHEDULING_MODE = os.environ.get('SCHEDULING_MODE', 'cost_optimized')  # cost_optimized|aggressive|conservative
BUSINESS_HOURS_ONLY = os.environ.get('BUSINESS_HOURS_ONLY', 'false').lower() == 'true'

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

# Protection configuration
PROTECTION_CONFIG = {
    'enabled': os.environ.get('PROTECTION_ENABLED', 'true').lower() == 'true',
    'config_source': os.environ.get('CONFIG_SOURCE', 'env'),  # env, s3, default
    's3_config_bucket': os.environ.get('CONFIG_S3_BUCKET', ''),
    's3_config_key': os.environ.get('CONFIG_S3_KEY', 'protection-config.json'),
    'override_enabled': os.environ.get('OVERRIDE_ENABLED', 'false').lower() == 'true',
    'rules': {}
}

# Default protection rules
DEFAULT_PROTECTION_RULES = {
    'ec2': {
        'whitelist_patterns': [],  # Resources to always protect
        'blacklist_patterns': ['*production*', '*critical*', '*do-not-delete*'],
        'protected_tags': {
            'Environment': ['production', 'prod'],
            'Protected': ['true', 'yes', '1'],
            'ManagedBy': ['terraform', 'cloudformation']
        },
        'protected_instance_types': ['t2.micro', 't3.micro'],  # Free tier instances
        'regex_patterns': [],
        'severity_levels': {
            'critical': {'action': 'never_delete'},
            'important': {'action': 'require_confirmation'},
            'standard': {'action': 'normal'}
        }
    },
    'rds': {
        'blacklist_patterns': ['*production*', '*master*', '*primary*'],
        'protected_tags': {
            'Environment': ['production', 'prod'],
            'Protected': ['true', 'yes', '1']
        },
        'protected_engine_types': [],
        'regex_patterns': []
    },
    's3': {
        'blacklist_patterns': ['*terraform-state*', '*cloudtrail*', '*logs*', '*backup*', '*config*'],
        'protected_tags': {
            'Environment': ['production', 'prod'],
            'Protected': ['true', 'yes', '1']
        },
        'regex_patterns': [r'^aws-', r'-prod-', r'-production-']
    },
    'elb': {
        'blacklist_patterns': ['*production*', '*critical*', '*public*'],
        'protected_tags': {
            'Environment': ['production', 'prod']
        },
        'regex_patterns': []
    },
    'elasticsearch': {
        'blacklist_patterns': ['*production*', '*logs*', '*analytics*'],
        'protected_tags': {
            'Environment': ['production', 'prod']
        },
        'regex_patterns': []
    }
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
                
                # Get all tags as dictionary
                tags_dict = {tag['Key']: tag['Value'] for tag in instance.get('Tags', [])}
                
                # Check protection status
                is_protected, protection_reason = is_resource_protected(
                    'ec2', 
                    name, 
                    instance_id,
                    tags_dict,
                    {'instance_type': instance['InstanceType']}
                )
                
                if is_protected:
                    structured_log('INFO', "EC2 instance protected from cleanup",
                                 resource_type='ec2',
                                 resource_id=instance_id,
                                 resource_name=name,
                                 region=region,
                                 age_days=age_days,
                                 protection_reason=protection_reason)
                    continue
                
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
                
                # Get tags for RDS instance
                try:
                    tag_response = rds_client.list_tags_for_resource(
                        ResourceName=db['DBInstanceArn']
                    )
                    tags_dict = {tag['Key']: tag['Value'] for tag in tag_response.get('TagList', [])}
                except Exception:
                    tags_dict = {}
                
                # Check protection status
                is_protected, protection_reason = is_resource_protected(
                    'rds',
                    db_id,
                    db['DBInstanceArn'],
                    tags_dict,
                    {'engine': db.get('Engine', 'unknown')}
                )
                
                if is_protected:
                    structured_log('INFO', "RDS instance protected from cleanup",
                                 resource_type='rds',
                                 resource_id=db_id,
                                 region=region,
                                 age_days=age_days,
                                 protection_reason=protection_reason)
                    continue
                
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

def get_resource_cost_estimate(resource_type: str, resource_info: Dict[str, Any], region: str) -> float:
    """Estimate monthly cost for a resource"""
    try:
        # Simplified cost estimates - in production would use AWS Cost Explorer API
        cost_map = {
            'ec2': {
                't2.micro': 8.50,
                't2.small': 17.00,
                't2.medium': 34.00,
                't2.large': 68.00,
                't3.micro': 7.60,
                't3.small': 15.20,
                't3.medium': 30.40,
                't3.large': 60.80,
                'm5.large': 70.00,
                'm5.xlarge': 140.00,
                'c5.large': 62.00,
                'c5.xlarge': 124.00
            },
            'rds': {
                'db.t2.micro': 13.00,
                'db.t2.small': 26.00,
                'db.t3.micro': 12.00,
                'db.t3.small': 24.00,
                'db.m5.large': 115.00,
                'db.m5.xlarge': 230.00
            },
            'nat_gateway': 45.00,  # Fixed cost per NAT gateway
            'elb': 25.00,  # Approximate ELB cost
            's3': 0.023,  # Per GB stored
            'elasticsearch': {
                't2.small.elasticsearch': 37.00,
                't2.medium.elasticsearch': 74.00,
                'm5.large.elasticsearch': 142.00
            }
        }
        
        if resource_type == 'ec2':
            instance_type = resource_info.get('instance_type', 't2.micro')
            return cost_map['ec2'].get(instance_type, 50.00)  # Default estimate
            
        elif resource_type == 'rds':
            instance_class = resource_info.get('instance_class', 'db.t2.micro')
            return cost_map['rds'].get(instance_class, 30.00)
            
        elif resource_type == 'nat_gateway':
            return cost_map['nat_gateway']
            
        elif resource_type == 'elb':
            return cost_map['elb']
            
        elif resource_type == 's3':
            # Estimate based on bucket size (simplified)
            return resource_info.get('size_gb', 10) * cost_map['s3']
            
        elif resource_type == 'elasticsearch':
            instance_type = resource_info.get('instance_type', 't2.small.elasticsearch')
            return cost_map['elasticsearch'].get(instance_type, 50.00)
            
        return 10.00  # Default estimate for unknown resources
        
    except Exception as e:
        structured_log('WARNING', f"Failed to estimate cost for {resource_type}", error=str(e))
        return 10.00

def analyze_cost_impact(summary: Dict[str, Any]) -> Dict[str, Any]:
    """Analyze cost impact of cleanup operations"""
    cost_analysis = {
        'total_monthly_savings': 0.0,
        'high_value_resources': [],
        'cost_by_type': {},
        'cost_by_region': defaultdict(float),
        'recommendations': []
    }
    
    try:
        # Analyze EC2 instances
        for instance in summary.get('ec2_instances', []):
            cost = get_resource_cost_estimate('ec2', {
                'instance_type': instance.get('type')
            }, instance.get('region'))
            
            cost_analysis['total_monthly_savings'] += cost
            cost_analysis['cost_by_type']['ec2'] = cost_analysis['cost_by_type'].get('ec2', 0) + cost
            cost_analysis['cost_by_region'][instance.get('region')] += cost
            
            if cost >= HIGH_VALUE_THRESHOLD:
                cost_analysis['high_value_resources'].append({
                    'type': 'ec2',
                    'id': instance['id'],
                    'name': instance.get('name'),
                    'monthly_cost': cost
                })
        
        # Analyze RDS instances
        for db in summary.get('rds_instances', []):
            cost = get_resource_cost_estimate('rds', {
                'instance_class': db.get('class')
            }, db.get('region'))
            
            cost_analysis['total_monthly_savings'] += cost
            cost_analysis['cost_by_type']['rds'] = cost_analysis['cost_by_type'].get('rds', 0) + cost
            cost_analysis['cost_by_region'][db.get('region')] += cost
            
            if cost >= HIGH_VALUE_THRESHOLD:
                cost_analysis['high_value_resources'].append({
                    'type': 'rds',
                    'id': db['id'],
                    'monthly_cost': cost
                })
        
        # Analyze NAT gateways
        for nat in summary.get('nat_gateways', []):
            cost = get_resource_cost_estimate('nat_gateway', {}, nat.get('region'))
            cost_analysis['total_monthly_savings'] += cost
            cost_analysis['cost_by_type']['nat_gateway'] = cost_analysis['cost_by_type'].get('nat_gateway', 0) + cost
            cost_analysis['cost_by_region'][nat.get('region')] += cost
        
        # Generate recommendations
        if cost_analysis['total_monthly_savings'] > 500:
            cost_analysis['recommendations'].append(
                f"High cost impact detected: ${cost_analysis['total_monthly_savings']:.2f}/month potential savings"
            )
        
        if cost_analysis['high_value_resources']:
            cost_analysis['recommendations'].append(
                f"Found {len(cost_analysis['high_value_resources'])} high-value resources requiring approval"
            )
        
        # Publish cost metrics
        if COST_ANALYSIS_ENABLED:
            publish_cloudwatch_metric('EstimatedMonthlySavings', 
                                     cost_analysis['total_monthly_savings'],
                                     unit='None')
            publish_cloudwatch_metric('HighValueResourcesFound', 
                                     len(cost_analysis['high_value_resources']),
                                     unit='Count')
        
    except Exception as e:
        log_error("Failed to analyze cost impact", e)
    
    return cost_analysis

def should_cleanup_based_on_schedule() -> bool:
    """Determine if cleanup should run based on scheduling configuration"""
    now = datetime.now(timezone.utc)
    
    # Business hours check (9 AM - 5 PM UTC)
    if BUSINESS_HOURS_ONLY:
        hour = now.hour
        if hour < 9 or hour >= 17:
            structured_log('INFO', "Skipping cleanup - outside business hours",
                         current_hour=hour,
                         business_hours="9-17 UTC")
            return False
    
    # Cost-optimized scheduling (run at optimal times for cost savings)
    if SCHEDULING_MODE == 'cost_optimized':
        # Run at end of day to maximize daily cost savings
        if now.hour not in [20, 21, 22, 23]:  # 8 PM - 11 PM UTC
            structured_log('DEBUG', "Not optimal time for cost-optimized cleanup",
                         current_hour=now.hour,
                         optimal_hours="20-23 UTC")
            # Still allow execution but log it
    
    elif SCHEDULING_MODE == 'conservative':
        # Only run once per day at specific time
        if now.hour != 22:  # 10 PM UTC only
            structured_log('INFO', "Conservative mode - skipping non-scheduled hour",
                         current_hour=now.hour,
                         scheduled_hour=22)
            return False
    
    # aggressive mode runs always
    return True

def prioritize_resources_by_cost(summary: Dict[str, Any]) -> Dict[str, Any]:
    """Prioritize resources for cleanup based on cost impact"""
    if not COST_ANALYSIS_ENABLED:
        return summary
    
    try:
        # Calculate cost for each resource and sort by cost
        for resource_type in ['ec2_instances', 'rds_instances', 'nat_gateways']:
            if resource_type in summary:
                resources = summary[resource_type]
                for resource in resources:
                    if resource_type == 'ec2_instances':
                        resource['estimated_cost'] = get_resource_cost_estimate(
                            'ec2', 
                            {'instance_type': resource.get('type')},
                            resource.get('region')
                        )
                    elif resource_type == 'rds_instances':
                        resource['estimated_cost'] = get_resource_cost_estimate(
                            'rds',
                            {'instance_class': resource.get('class')},
                            resource.get('region')
                        )
                    elif resource_type == 'nat_gateways':
                        resource['estimated_cost'] = get_resource_cost_estimate(
                            'nat_gateway', {}, resource.get('region')
                        )
                
                # Sort by cost (highest first)
                summary[resource_type] = sorted(
                    resources,
                    key=lambda x: x.get('estimated_cost', 0),
                    reverse=True
                )
        
        structured_log('INFO', "Resources prioritized by cost impact",
                      scheduling_mode=SCHEDULING_MODE)
        
    except Exception as e:
        log_error("Failed to prioritize resources by cost", e)
    
    return summary

def load_protection_config() -> Dict[str, Any]:
    """Load protection configuration from various sources"""
    global PROTECTION_CONFIG
    
    try:
        if PROTECTION_CONFIG['config_source'] == 's3' and PROTECTION_CONFIG['s3_config_bucket']:
            # Load from S3
            structured_log('INFO', "Loading protection config from S3",
                         bucket=PROTECTION_CONFIG['s3_config_bucket'],
                         key=PROTECTION_CONFIG['s3_config_key'])
            
            s3_client = boto3.client('s3')
            response = s3_client.get_object(
                Bucket=PROTECTION_CONFIG['s3_config_bucket'],
                Key=PROTECTION_CONFIG['s3_config_key']
            )
            config_data = json.loads(response['Body'].read())
            PROTECTION_CONFIG['rules'] = validate_protection_config(config_data)
            PROTECTION_CONFIG['config_source'] = 's3'
            
        elif PROTECTION_CONFIG['config_source'] == 'env':
            # Load from environment variables
            structured_log('INFO', "Loading protection config from environment")
            PROTECTION_CONFIG['rules'] = DEFAULT_PROTECTION_RULES
            PROTECTION_CONFIG['config_source'] = 'env'
            
        else:
            # Use default configuration
            structured_log('INFO', "Using default protection config")
            PROTECTION_CONFIG['rules'] = DEFAULT_PROTECTION_RULES
            PROTECTION_CONFIG['config_source'] = 'default'
            
    except Exception as e:
        log_error("Failed to load protection config, using defaults", e)
        PROTECTION_CONFIG['rules'] = DEFAULT_PROTECTION_RULES
        PROTECTION_CONFIG['config_source'] = 'default_fallback'
    
    return PROTECTION_CONFIG

def validate_protection_config(config: Dict[str, Any]) -> Dict[str, Any]:
    """Validate protection configuration structure"""
    validated = {}
    
    for resource_type in ['ec2', 'rds', 's3', 'elb', 'elasticsearch']:
        if resource_type in config:
            validated[resource_type] = {
                'whitelist_patterns': config[resource_type].get('whitelist_patterns', []),
                'blacklist_patterns': config[resource_type].get('blacklist_patterns', []),
                'protected_tags': config[resource_type].get('protected_tags', {}),
                'regex_patterns': config[resource_type].get('regex_patterns', []),
                'severity_levels': config[resource_type].get('severity_levels', {})
            }
        else:
            # Use defaults if resource type not in config
            validated[resource_type] = DEFAULT_PROTECTION_RULES.get(resource_type, {})
    
    return validated

def match_pattern(value: str, pattern: str) -> bool:
    """Match a value against a pattern (supports wildcards)"""
    if not value or not pattern:
        return False
    
    # Convert wildcard pattern to regex
    if '*' in pattern:
        regex_pattern = pattern.replace('*', '.*')
        regex_pattern = f'^{regex_pattern}$'
        return bool(re.match(regex_pattern, value, re.IGNORECASE))
    
    # Exact match (case-insensitive)
    return value.lower() == pattern.lower()

def match_regex_patterns(value: str, patterns: List[str]) -> bool:
    """Check if value matches any regex pattern"""
    for pattern in patterns:
        try:
            if re.search(pattern, value, re.IGNORECASE):
                return True
        except re.error:
            structured_log('WARNING', f"Invalid regex pattern: {pattern}")
    return False

def check_tag_protection(tags: Dict[str, str], protected_tags: Dict[str, List[str]]) -> tuple[bool, str]:
    """Check if resource tags indicate protection"""
    if not tags or not protected_tags:
        return False, ""
    
    for tag_key, protected_values in protected_tags.items():
        if tag_key in tags:
            tag_value = tags[tag_key].lower()
            for protected_value in protected_values:
                if tag_value == protected_value.lower():
                    return True, f"Protected by tag {tag_key}={tags[tag_key]}"
    
    return False, ""

def is_resource_protected(resource_type: str, resource_name: str, 
                         resource_id: str = None, tags: Dict[str, str] = None,
                         additional_checks: Dict[str, Any] = None) -> tuple[bool, str]:
    """
    Comprehensive protection check for resources
    Returns: (is_protected, reason)
    """
    if not PROTECTION_CONFIG['enabled']:
        return False, ""
    
    rules = PROTECTION_CONFIG['rules'].get(resource_type, {})
    if not rules:
        return False, ""
    
    # Check whitelist (always protect)
    whitelist = rules.get('whitelist_patterns', [])
    for pattern in whitelist:
        if match_pattern(resource_name, pattern):
            return True, f"Whitelisted by pattern: {pattern}"
    
    # Check blacklist (always protect)
    blacklist = rules.get('blacklist_patterns', [])
    for pattern in blacklist:
        if match_pattern(resource_name, pattern):
            return True, f"Blacklisted by pattern: {pattern}"
    
    # Check regex patterns
    regex_patterns = rules.get('regex_patterns', [])
    if match_regex_patterns(resource_name, regex_patterns):
        return True, f"Protected by regex pattern"
    
    # Check tag-based protection
    if tags:
        protected, reason = check_tag_protection(tags, rules.get('protected_tags', {}))
        if protected:
            return True, reason
    
    # Resource-specific checks
    if additional_checks and resource_type == 'ec2':
        # Check instance type protection
        instance_type = additional_checks.get('instance_type')
        protected_types = rules.get('protected_instance_types', [])
        if instance_type and instance_type in protected_types:
            return True, f"Protected instance type: {instance_type}"
    
    return False, ""

def is_resource_excluded(resource_name: str, exclusion_patterns: List[str]) -> bool:
    """Check if resource name matches any exclusion pattern (legacy function)"""
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
    
    # Load protection configuration
    load_protection_config()
    
    # Check scheduling constraints
    if not should_cleanup_based_on_schedule():
        structured_log('INFO', "Cleanup skipped due to scheduling constraints",
                      scheduling_mode=SCHEDULING_MODE,
                      business_hours_only=BUSINESS_HOURS_ONLY)
        return {
            'statusCode': 200,
            'body': json.dumps({
                'message': "Cleanup skipped - outside scheduled window",
                'correlation_id': CORRELATION_ID,
                'scheduling_mode': SCHEDULING_MODE
            })
        }
    
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
    
    # Prioritize resources by cost if enabled
    if COST_ANALYSIS_ENABLED:
        summary = prioritize_resources_by_cost(summary)
    
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
    
    # Perform cost impact analysis
    cost_analysis = {}
    if COST_ANALYSIS_ENABLED:
        cost_analysis = analyze_cost_impact(summary)
        summary['cost_analysis'] = cost_analysis
    
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
    
    # Get cost analysis data
    cost_analysis = summary.get('cost_analysis', {})
    monthly_savings = cost_analysis.get('total_monthly_savings', 0)
    high_value_resources = cost_analysis.get('high_value_resources', [])
    
    message = f"""AWS Auto-Shutdown Report
{'=' * 50}
Mode: {'DRY RUN' if DRY_RUN else 'EXECUTED'}
Time: {summary['timestamp']}
Correlation ID: {summary.get('correlation_id', 'N/A')}
Max Age: {MAX_AGE_DAYS} days
Scheduling Mode: {SCHEDULING_MODE}

Resources Summary:
- EC2 Instances: {len(summary['ec2_instances'])}
- RDS Instances: {len(summary['rds_instances'])}
- ECS Services: {len(summary['ecs_services'])}
- NAT Gateways: {len(summary['nat_gateways'])}
- Load Balancers: {len(summary.get('load_balancers', []))}
- S3 Buckets: {len(summary.get('s3_buckets', []))}
- Elasticsearch Domains: {len(summary.get('elasticsearch_domains', []))}

Cost Analysis:
- Estimated Monthly Savings: ${monthly_savings:.2f}
- High-Value Resources: {len(high_value_resources)}
- Cost-Optimized Scheduling: {SCHEDULING_MODE}

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