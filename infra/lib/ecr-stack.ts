import * as cdk from 'aws-cdk-lib';
import * as ecr from 'aws-cdk-lib/aws-ecr';
import { Construct } from 'constructs';
import { ECR_REPO_NAME } from '../config/environments';

export class EcrStack extends cdk.Stack {
  public readonly repository: ecr.IRepository;

  constructor(scope: Construct, id: string, props?: cdk.StackProps) {
    super(scope, id, props);

    // Import the existing ECR repository — do not recreate it.
    this.repository = ecr.Repository.fromRepositoryName(
      this,
      'GigaMcpServerRepo',
      ECR_REPO_NAME,
    );

    // Lifecycle rules can be added manually via the console or CLI since
    // CDK cannot attach a lifecycle policy to an imported (fromRepositoryName) repository.
  }
}
