How do you just#!/usr/bin/env python3

"""
AWS Resource Age Report
Generates a detailed report of all running resources and their ages
"""

import boto3
import json
from datetime import datetime, timezone
from collections import defaultdict
import argparse

def get_age_days(launch_time):
    """Calculate age in days from launch time"""
    if isinstance(launch_time, str):
        launch_time = datetime.fromisoformat(launch_time.replace('Z', '+00:00'))
    current_time = datetime.now(timezone.utc)
    age = current_time - launch_time
    return age.days

def get_ec2_instances(ec2_client, region):
    """Get all running EC2 instances with age info"""
    instances = []
    response = ec2_client.describe_instances(
        Filters=[{'Name': 'instance-state-name', 'Values': ['running']}]
    )
    
    for reservation in response['Reservations']:
        for instance in reservation['Instances']:
            name = next((tag['Value'] for tag in instance.get('Tags', []) if tag['Key'] == 'Name'), 'Unnamed')
            instances.append({
                'Type': 'EC2',
                'Region': region,
                'Id': instance['InstanceId'],
                'Name': name,
                'InstanceType': instance['InstanceType'],
                'LaunchTime': instance['LaunchTime'],
                'AgeDays': get_age_days(instance['LaunchTime']),
                'EstimatedDailyCost': estimate_ec2_cost(instance['InstanceType'])
            })
    
    return instances

def get_rds_instances(rds_client, region):
    """Get all available RDS instances with age info"""
    instances = []
    response = rds_client.describe_db_instances()
    
    for db in response['DBInstances']:
        if db['DBInstanceStatus'] == 'available':
            instances.append({
                'Type': 'RDS',
                'Region': region,
                'Id': db['DBInstanceIdentifier'],
                'Name': db['DBInstanceIdentifier'],
                'InstanceType': db['DBInstanceClass'],
                'LaunchTime': db['InstanceCreateTime'],
                'AgeDays': get_age_days(db['InstanceCreateTime']),
                'EstimatedDailyCost': estimate_rds_cost(db['DBInstanceClass'])
            })
    
    return instances

def get_ecs_services(ecs_client, region):
    """Get all ECS services with running tasks"""
    services = []
    
    # Get all clusters
    clusters = ecs_client.list_clusters()['clusterArns']
    
    for cluster in clusters:
        # Get services in cluster
        service_arns = ecs_client.list_services(cluster=cluster)['serviceArns']
        
        if service_arns:
            service_details = ecs_client.describe_services(
                cluster=cluster,
                services=service_arns
            )['services']
            
            for service in service_details:
                if service['desiredCount'] > 0:
                    services.append({
                        'Type': 'ECS',
                        'Region': region,
                        'Id': service['serviceName'],
                        'Name': service['serviceName'],
                        'Cluster': cluster.split('/')[-1],
                        'DesiredCount': service['desiredCount'],
                        'LaunchTime': service['createdAt'],
                        'AgeDays': get_age_days(service['createdAt']),
                        'EstimatedDailyCost': estimate_ecs_cost(service['desiredCount'])
                    })
    
    return services

def estimate_ec2_cost(instance_type):
    """Rough estimate of EC2 daily costs"""
    # Very rough estimates - actual costs vary by region
    cost_map = {
        't2.micro': 0.28,
        't2.small': 0.55,
        't2.medium': 1.10,
        't3.micro': 0.25,
        't3.small': 0.50,
        't3.medium': 1.00,
        'm5.large': 2.30,
        'm5.xlarge': 4.60,
        'c5.large': 2.04,
        'c5.xlarge': 4.08
    }
    return cost_map.get(instance_type, 5.00)  # Default $5/day for unknown types

def estimate_rds_cost(instance_class):
    """Rough estimate of RDS daily costs"""
    cost_map = {
        'db.t2.micro': 0.43,
        'db.t2.small': 0.86,
        'db.t3.micro': 0.43,
        'db.t3.small': 0.86,
        'db.m5.large': 4.13,
        'db.m5.xlarge': 8.26
    }
    return cost_map.get(instance_class, 10.00)  # Default $10/day for unknown types

def estimate_ecs_cost(task_count):
    """Rough estimate of ECS Fargate costs"""
    # Assuming 0.25 vCPU and 0.5GB per task
    return task_count * 0.75  # ~$0.75/day per task

def generate_report(resources, max_age_days):
    """Generate shutdown report"""
    report = {
        'generated_at': datetime.now(timezone.utc).isoformat(),
        'max_age_days': max_age_days,
        'summary': {
            'total_resources': len(resources),
            'resources_to_shutdown': 0,
            'estimated_daily_savings': 0
        },
        'resources_by_type': defaultdict(list),
        'resources_to_shutdown': []
    }
    
    for resource in resources:
        report['resources_by_type'][resource['Type']].append(resource)
        
        if resource['AgeDays'] >= max_age_days:
            report['resources_to_shutdown'].append(resource)
            report['summary']['resources_to_shutdown'] += 1
            report['summary']['estimated_daily_savings'] += resource.get('EstimatedDailyCost', 0)
    
    return report

def main():
    parser = argparse.ArgumentParser(description='Generate AWS resource age report')
    parser.add_argument('--regions', nargs='+', 
                       default=['us-east-1', 'us-west-2', 'ap-southeast-2', 'ap-southeast-4'],
                       help='AWS regions to check')
    parser.add_argument('--max-age-days', type=int, default=3,
                       help='Maximum age in days before flagging for shutdown')
    parser.add_argument('--output', default='aws-resources-report.json',
                       help='Output file for the report')
    
    args = parser.parse_args()
    
    all_resources = []
    
    print(f"Scanning AWS resources older than {args.max_age_days} days...")
    print(f"Regions: {', '.join(args.regions)}")
    print()
    
    for region in args.regions:
        print(f"Scanning {region}...")
        
        # EC2
        try:
            ec2_client = boto3.client('ec2', region_name=region)
            resources = get_ec2_instances(ec2_client, region)
            all_resources.extend(resources)
            print(f"  Found {len(resources)} EC2 instances")
        except Exception as e:
            print(f"  Error scanning EC2: {e}")
        
        # RDS
        try:
            rds_client = boto3.client('rds', region_name=region)
            resources = get_rds_instances(rds_client, region)
            all_resources.extend(resources)
            print(f"  Found {len(resources)} RDS instances")
        except Exception as e:
            print(f"  Error scanning RDS: {e}")
        
        # ECS
        try:
            ecs_client = boto3.client('ecs', region_name=region)
            resources = get_ecs_services(ecs_client, region)
            all_resources.extend(resources)
            print(f"  Found {len(resources)} ECS services")
        except Exception as e:
            print(f"  Error scanning ECS: {e}")
    
    # Generate report
    report = generate_report(all_resources, args.max_age_days)
    
    # Save report
    with open(args.output, 'w') as f:
        json.dump(report, f, indent=2, default=str)
    
    # Print summary
    print()
    print("=" * 50)
    print("SUMMARY")
    print("=" * 50)
    print(f"Total resources found: {report['summary']['total_resources']}")
    print(f"Resources older than {args.max_age_days} days: {report['summary']['resources_to_shutdown']}")
    print(f"Estimated daily savings: ${report['summary']['estimated_daily_savings']:.2f}")
    print()
    
    if report['resources_to_shutdown']:
        print("Resources to be shut down:")
        for resource in report['resources_to_shutdown']:
            print(f"  - {resource['Type']} {resource['Id']} ({resource['Name']}) "
                  f"in {resource['Region']} - {resource['AgeDays']} days old "
                  f"(~${resource.get('EstimatedDailyCost', 0):.2f}/day)")
    
    print()
    print(f"Full report saved to: {args.output}")

if __name__ == '__main__':
    main()