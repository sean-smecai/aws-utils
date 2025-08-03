#!/bin/bash

# AWS Auto-Shutdown Script
# This script shuts down AWS resources that are older than a specified number of days

set -euo pipefail

# Default values
DRY_RUN=true
MAX_AGE_DAYS=3
REGIONS=("us-east-1" "us-west-2" "ap-southeast-2" "ap-southeast-4")
REPORT_FILE="aws-resources-report.json"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Function to print colored output
print_status() {
    local color=$1
    local message=$2
    echo -e "${color}${message}${NC}"
}

# Function to show usage
show_usage() {
    echo "Usage: $0 [OPTIONS]"
    echo ""
    echo "Options:"
    echo "  --dry-run              Run in dry-run mode (default: true)"
    echo "  --execute              Actually shut down resources (overrides --dry-run)"
    echo "  --max-age-days DAYS    Maximum age in days before shutdown (default: 3)"
    echo "  --regions REGIONS      Comma-separated list of regions (default: us-east-1,us-west-2,ap-southeast-2,ap-southeast-4)"
    echo "  --report-file FILE     Report file to read (default: aws-resources-report.json)"
    echo "  -h, --help             Show this help message"
    echo ""
    echo "Examples:"
    echo "  $0 --dry-run --max-age-days 7"
    echo "  $0 --execute --max-age-days 3"
}

# Parse command line arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        --dry-run)
            DRY_RUN=true
            shift
            ;;
        --execute)
            DRY_RUN=false
            shift
            ;;
        --max-age-days)
            MAX_AGE_DAYS="$2"
            shift 2
            ;;
        --regions)
            IFS=',' read -ra REGIONS <<< "$2"
            shift 2
            ;;
        --report-file)
            REPORT_FILE="$2"
            shift 2
            ;;
        -h|--help)
            show_usage
            exit 0
            ;;
        *)
            print_status $RED "Unknown option: $1"
            show_usage
            exit 1
            ;;
    esac
done

# Check if report file exists
if [[ ! -f "$REPORT_FILE" ]]; then
    print_status $RED "Report file not found: $REPORT_FILE"
    print_status $YELLOW "Please run the report generation script first:"
    print_status $YELLOW "python scripts/aws-shutdown-report.py --max-age-days $MAX_AGE_DAYS --output $REPORT_FILE"
    exit 1
fi

# Check if jq is available
if ! command -v jq &> /dev/null; then
    print_status $RED "jq is required but not installed. Please install jq first."
    exit 1
fi

# Check if AWS CLI is available
if ! command -v aws &> /dev/null; then
    print_status $RED "AWS CLI is required but not installed. Please install AWS CLI first."
    exit 1
fi

# Function to shut down EC2 instances
shutdown_ec2_instances() {
    local region=$1
    local instances=$2
    
    if [[ -z "$instances" ]]; then
        return
    fi
    
    print_status $BLUE "Processing EC2 instances in $region..."
    
    for instance_id in $instances; do
        if [[ "$DRY_RUN" == "true" ]]; then
            print_status $YELLOW "  [DRY RUN] Would stop EC2 instance: $instance_id"
        else
            print_status $GREEN "  Stopping EC2 instance: $instance_id"
            aws ec2 stop-instances --instance-ids "$instance_id" --region "$region" > /dev/null
            
            # Wait for instance to stop
            aws ec2 wait instance-stopped --instance-ids "$instance_id" --region "$region"
            print_status $GREEN "  ✓ EC2 instance $instance_id stopped successfully"
        fi
    done
}

# Function to shut down RDS instances
shutdown_rds_instances() {
    local region=$1
    local instances=$2
    
    if [[ -z "$instances" ]]; then
        return
    fi
    
    print_status $BLUE "Processing RDS instances in $region..."
    
    for instance_id in $instances; do
        if [[ "$DRY_RUN" == "true" ]]; then
            print_status $YELLOW "  [DRY RUN] Would stop RDS instance: $instance_id"
        else
            print_status $GREEN "  Stopping RDS instance: $instance_id"
            aws rds stop-db-instance --db-instance-identifier "$instance_id" --region "$region" > /dev/null
            
            # Wait for instance to stop
            aws rds wait db-instance-stopped --db-instance-identifier "$instance_id" --region "$region"
            print_status $GREEN "  ✓ RDS instance $instance_id stopped successfully"
        fi
    done
}

# Function to shut down ECS services
shutdown_ecs_services() {
    local region=$1
    local services=$2
    
    if [[ -z "$services" ]]; then
        return
    fi
    
    print_status $BLUE "Processing ECS services in $region..."
    
    for service_info in $services; do
        # Parse service info (format: cluster:service)
        IFS=':' read -r cluster service <<< "$service_info"
        
        if [[ "$DRY_RUN" == "true" ]]; then
            print_status $YELLOW "  [DRY RUN] Would scale down ECS service: $service in cluster $cluster"
        else
            print_status $GREEN "  Scaling down ECS service: $service in cluster $cluster"
            aws ecs update-service \
                --cluster "$cluster" \
                --service "$service" \
                --desired-count 0 \
                --region "$region" > /dev/null
            
            print_status $GREEN "  ✓ ECS service $service scaled down successfully"
        fi
    done
}

# Main execution
main() {
    print_status $BLUE "AWS Auto-Shutdown Script"
    print_status $BLUE "========================"
    print_status $BLUE "Mode: $([ "$DRY_RUN" == "true" ] && echo "DRY RUN" || echo "EXECUTE")"
    print_status $BLUE "Max Age: $MAX_AGE_DAYS days"
    print_status $BLUE "Regions: ${REGIONS[*]}"
    print_status $BLUE "Report File: $REPORT_FILE"
    echo ""
    
    # Read the report file
    if [[ ! -f "$REPORT_FILE" ]]; then
        print_status $RED "Report file not found: $REPORT_FILE"
        exit 1
    fi
    
    # Get resources to shutdown from the report
    resources_to_shutdown=$(jq -r '.resources_to_shutdown[] | "\(.Type):\(.Region):\(.Id):\(.Name)"' "$REPORT_FILE" 2>/dev/null || echo "")
    
    if [[ -z "$resources_to_shutdown" ]]; then
        print_status $GREEN "No resources found that need to be shut down."
        exit 0
    fi
    
    # Group resources by type and region
    declare -A ec2_instances
    declare -A rds_instances
    declare -A ecs_services
    
    while IFS=: read -r type region id name; do
        case $type in
            "EC2")
                ec2_instances["$region"]="${ec2_instances[$region]:-} $id"
                ;;
            "RDS")
                rds_instances["$region"]="${rds_instances[$region]:-} $id"
                ;;
            "ECS")
                # For ECS, we need cluster info from the report
                cluster=$(jq -r --arg id "$id" --arg region "$region" '.resources_to_shutdown[] | select(.Type == "ECS" and .Id == $id and .Region == $region) | .Cluster' "$REPORT_FILE")
                ecs_services["$region"]="${ecs_services[$region]:-} $cluster:$id"
                ;;
        esac
    done <<< "$resources_to_shutdown"
    
    # Process each region
    for region in "${REGIONS[@]}"; do
        print_status $BLUE "Processing region: $region"
        echo ""
        
        # Shutdown EC2 instances
        if [[ -n "${ec2_instances[$region]:-}" ]]; then
            shutdown_ec2_instances "$region" "${ec2_instances[$region]}"
        fi
        
        # Shutdown RDS instances
        if [[ -n "${rds_instances[$region]:-}" ]]; then
            shutdown_rds_instances "$region" "${rds_instances[$region]}"
        fi
        
        # Shutdown ECS services
        if [[ -n "${ecs_services[$region]:-}" ]]; then
            shutdown_ecs_services "$region" "${ecs_services[$region]}"
        fi
        
        echo ""
    done
    
    # Summary
    total_resources=$(echo "$resources_to_shutdown" | wc -l)
    print_status $GREEN "Summary:"
    print_status $GREEN "  Total resources processed: $total_resources"
    
    if [[ "$DRY_RUN" == "true" ]]; then
        print_status $YELLOW "  This was a dry run. No resources were actually shut down."
        print_status $YELLOW "  Run with --execute to actually shut down resources."
    else
        print_status $GREEN "  All resources have been shut down successfully."
    fi
}

# Run main function
main "$@"