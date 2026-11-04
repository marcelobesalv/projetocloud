set -euo pipefail
export AWS_DEFAULT_REGION=us-east-2
P=setup-pronto
ST=~/sp.env
: > $ST
save(){ echo "$1=$2" >> $ST; echo "$1=$2"; }
tag(){ echo "ResourceType=$1,Tags=[{Key=Name,Value=$P-$2},{Key=Project,Value=$P}]"; }
AZ1=$(aws ec2 describe-availability-zones --query 'AvailabilityZones[0].ZoneName' --output text)
AZ2=$(aws ec2 describe-availability-zones --query 'AvailabilityZones[1].ZoneName' --output text)
save AZ1 $AZ1; save AZ2 $AZ2
VPC=$(aws ec2 create-vpc --cidr-block 10.20.0.0/16 --tag-specifications "$(tag vpc vpc)" --query Vpc.VpcId --output text); save VPC $VPC
aws ec2 wait vpc-available --vpc-ids $VPC
aws ec2 modify-vpc-attribute --vpc-id $VPC --enable-dns-hostnames
IGW=$(aws ec2 create-internet-gateway --tag-specifications "$(tag internet-gateway igw)" --query InternetGateway.InternetGatewayId --output text); save IGW $IGW
aws ec2 attach-internet-gateway --internet-gateway-id $IGW --vpc-id $VPC
mk(){ aws ec2 create-subnet --vpc-id $VPC --cidr-block $1 --availability-zone $2 --tag-specifications "$(tag subnet $3)" --query Subnet.SubnetId --output text; }
save PUB1 $(mk 10.20.0.0/24 $AZ1 public-a); save PUB2 $(mk 10.20.1.0/24 $AZ2 public-b)
save APP1 $(mk 10.20.10.0/24 $AZ1 app-a); save APP2 $(mk 10.20.11.0/24 $AZ2 app-b)
save DATA1 $(mk 10.20.20.0/24 $AZ1 data-a); save DATA2 $(mk 10.20.21.0/24 $AZ2 data-b)
source $ST
rt(){ aws ec2 create-route-table --vpc-id $VPC --tag-specifications "$(tag route-table $1)" --query RouteTable.RouteTableId --output text; }
RTPUB=$(rt rt-public); save RTPUB $RTPUB
aws ec2 create-route --route-table-id $RTPUB --destination-cidr-block 0.0.0.0/0 --gateway-id $IGW >/dev/null
aws ec2 associate-route-table --route-table-id $RTPUB --subnet-id $PUB1 >/dev/null
aws ec2 associate-route-table --route-table-id $RTPUB --subnet-id $PUB2 >/dev/null
EIP=$(aws ec2 allocate-address --domain vpc --tag-specifications "$(tag elastic-ip nat-eip)" --query AllocationId --output text); save EIP $EIP
NAT=$(aws ec2 create-nat-gateway --subnet-id $PUB1 --allocation-id $EIP --tag-specifications "$(tag natgateway nat)" --query NatGateway.NatGatewayId --output text); save NAT $NAT
echo "aguardando NAT..."; aws ec2 wait nat-gateway-available --nat-gateway-ids $NAT
RTAPP=$(rt rt-app); save RTAPP $RTAPP
aws ec2 create-route --route-table-id $RTAPP --destination-cidr-block 0.0.0.0/0 --nat-gateway-id $NAT >/dev/null
aws ec2 associate-route-table --route-table-id $RTAPP --subnet-id $APP1 >/dev/null
aws ec2 associate-route-table --route-table-id $RTAPP --subnet-id $APP2 >/dev/null
RTDATA=$(rt rt-data); save RTDATA $RTDATA
aws ec2 associate-route-table --route-table-id $RTDATA --subnet-id $DATA1 >/dev/null
aws ec2 associate-route-table --route-table-id $RTDATA --subnet-id $DATA2 >/dev/null
S3EP=$(aws ec2 create-vpc-endpoint --vpc-id $VPC --service-name com.amazonaws.us-east-2.s3 --route-table-ids $RTPUB $RTAPP --tag-specifications "$(tag vpc-endpoint s3)" --query VpcEndpoint.VpcEndpointId --output text); save S3EP $S3EP
echo "BLOCO 1 OK"
